import hashlib
import json
import os
import re
import shutil
import zipfile
from datetime import timedelta
from html import escape
from pathlib import Path
from uuid import uuid4

from flask import current_app
from sqlalchemy import update

from app.extensions import db
from app.models import Job, Server, ServerBackup, ServerCpuRecovery, utcnow

from .alert_manager import enqueue, setting
from .security import audit
from .ssh_client import SSHFailure, connect, execute, stream_to

ENGINES = {"mysql", "mariadb", "postgresql"}
ARTIFACT = re.compile(r"backup-[0-9a-f]{32}\.zip")


def backup_command(server, mode):
    if server.ssh_public_key:
        return "serverwatch-db-" + mode
    return "/usr/bin/sudo -n /usr/local/sbin/serverwatch-db-export " + mode


def directory_for(server_id):
    return Path(current_app.instance_path) / "vps-backups" / str(server_id)


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class ReservedSpace:
    def __init__(self, target, directory):
        self.target = target
        self.directory = directory
        self.remaining = 0

    def write(self, block):
        if self.remaining < len(block):
            if shutil.disk_usage(self.directory).free < 512 * 1024**2:
                raise ValueError("Недостатньо вільного місця для резервної копії.")
            self.remaining = 8 * 1024**2
        self.remaining -= len(block)
        self.target.write(block)


def create_archive(server, directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = f"backup-{uuid4().hex}.zip"
    temporary = directory / (name + ".part")
    final = directory / name
    try:
        with connect(server) as client:
            engines = json.loads(execute(client, backup_command(server, "discover"), limit=2048))
            if (
                not isinstance(engines, list)
                or not engines
                or len(engines) > 3
                or any(not isinstance(engine, str) or engine not in ENGINES for engine in engines)
                or len(set(engines)) != len(engines)
            ):
                raise ValueError("Не знайдено доступного MySQL, MariaDB або PostgreSQL.")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for engine in engines:
                        with archive.open(f"{engine}.sql", "w", force_zip64=True) as entry:
                            stream_to(
                                client,
                                backup_command(server, engine),
                                ReservedSpace(entry, directory),
                                timeout=920,
                                limit=2 * 1024**3,
                            )
                output.flush()
                os.fsync(output.fileno())
        size = temporary.stat().st_size
        digest = checksum(temporary)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("Перевірка CRC архіву не пройдена.")
            for entry in archive.infolist():
                with archive.open(entry) as source:
                    header = source.read(512).lstrip()
                    if entry.filename == "mariadb.sql":
                        header = re.sub(rb"^/\*M?!999999\\-[^\r\n]*\*/\r?\n", b"", header).lstrip()
                    if entry.file_size < 64 or not header.startswith(b"--"):
                        raise ValueError("Архів містить порожній або некоректний SQL-дамп.")
        temporary.replace(final)
        if final.stat().st_size != size or checksum(final) != digest:
            final.unlink()
            raise ValueError("Контрольна сума архіву не збігається.")
        return name, size, digest, engines
    finally:
        temporary.unlink(missing_ok=True)


def claim_backup():
    now = utcnow()
    allowed = setting("automation_enabled", 0)
    backup = db.session.execute(
        db.select(ServerBackup.server_id, ServerBackup.status)
        .join(Server, Server.id == ServerBackup.server_id)
        .where(
            Server.is_active.is_(True),
            db.or_(Server.lease_until.is_(None), Server.lease_until < now),
            db.or_(
                ServerBackup.status == "running",
                db.and_(
                    ServerBackup.next_at <= now,
                    db.or_(
                        ServerBackup.requested_by.is_not(None),
                        db.and_(
                            allowed == 1, ServerBackup.enabled, Server.automation_enabled.is_(True)
                        ),
                    ),
                ),
            ),
            ~db.select(ServerCpuRecovery.server_id)
            .where(
                ServerCpuRecovery.server_id == Server.id,
                db.or_(
                    ServerCpuRecovery.verify_until.is_not(None),
                    ServerCpuRecovery.notification_id.is_not(None),
                    ServerCpuRecovery.manual_actor_id.is_not(None),
                ),
            )
            .exists(),
            ~db.select(Job.id)
            .where(
                Job.server_id == Server.id,
                Job.status == "pending",
                db.or_(Job.run_after.is_(None), Job.run_after <= now),
            )
            .exists(),
        )
        .order_by(ServerBackup.requested_by.is_(None), ServerBackup.next_at)
        .limit(1)
    ).first()
    if not backup:
        db.session.rollback()
        return None
    lease = now.replace(microsecond=0) + timedelta(minutes=50)
    claimed = db.session.execute(
        update(Server)
        .where(
            Server.id == backup.server_id,
            Server.is_active.is_(True),
            db.or_(Server.lease_until.is_(None), Server.lease_until < now),
        )
        .values(lease_until=lease)
    )
    if not claimed.rowcount:
        db.session.rollback()
        return None
    state = db.session.get(ServerBackup, backup.server_id, with_for_update=True)
    interrupted = state.status == "running"
    state.status = "running"
    db.session.commit()
    return backup.server_id, lease, interrupted


def run_backup(app, server_id, lease, interrupted):
    with app.app_context():
        server = db.session.get(Server, server_id)
        state = db.session.get(ServerBackup, server_id)
        actor = state.requested_by if state.requested_by is not None else "monitor"
        source = "manual" if state.requested_by is not None else "automatic"
        folder = directory_for(server_id)
        name = None
        try:
            if interrupted:
                raise ValueError("Резервне копіювання перервано.")
            if source == "automatic" and not (
                state.enabled and server.automation_enabled and setting("automation_enabled", 0)
            ):
                state.status = "cancelled"
                state.result = "Резервне копіювання скасовано в налаштуваннях."
            else:
                name, size, digest, engines = create_archive(server, folder)
                state.artifact, state.size, state.sha256, state.engines = (
                    name,
                    size,
                    digest,
                    engines,
                )
                state.last_success_at = utcnow()
                state.status = "done"
                state.result = "ZIP, SQL, розмір та SHA-256 перевірено."
        except (SSHFailure, OSError, ValueError, zipfile.BadZipFile) as error:
            state.status = "failed"
            state.result = (
                str(error)
                if type(error) is ValueError
                else "Не вдалося отримати або перевірити дамп. Перевірте SSH, helper та дисковий простір."
            )
            enqueue(server, f"{escape(server.name)} · помилка бекапу\n{escape(state.result)}")
        state.requested_by = None
        state.next_at = utcnow() + timedelta(days=1) if state.enabled else None
        audit(actor, "database_backup", state.result, server.id, source=source, status=state.status)
        db.session.commit()
        if name:
            try:
                for path in folder.iterdir():
                    if path.name != name and ARTIFACT.fullmatch(path.name.removesuffix(".part")):
                        path.unlink(missing_ok=True)
            except OSError:
                current_app.logger.warning("Не вдалося очистити попередній бекап VPS %s", server_id)
        db.session.execute(
            update(Server)
            .where(Server.id == server_id, Server.lease_until == lease)
            .values(lease_until=None, next_check_at=utcnow())
        )
        db.session.commit()
