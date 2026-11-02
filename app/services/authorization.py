import hashlib
import hmac
import secrets
from datetime import timedelta

from flask import current_app
from sqlalchemy import update

from app.extensions import db
from app.models import Server, utcnow

from .security import audit, blocked, failed, rate_key, require_admin


def code_hash(code):
    return hmac.new(
        current_app.config["SECRET_KEY"].encode(), code.encode(), hashlib.sha256
    ).hexdigest()


def issue_code(server):
    if server.ssh_public_key and server.installation_status not in ("READY", "ACTIVE"):
        raise ValueError("Спочатку дочекайтеся успішної SSH-перевірки.")
    for _ in range(10):
        code = f"{secrets.randbelow(10**15):015d}"
        digest = code_hash(code)
        if not db.session.scalar(
            db.select(Server.id).where(Server.authorization_code_hash == digest)
        ):
            server.authorization_code_hash = digest
            server.authorization_expires_at = utcnow() + timedelta(hours=24)
            return code
    raise RuntimeError("Не вдалося створити унікальний код.")


def authorize_group(code, group_id, user_id):
    require_admin(user_id)
    key = rate_key("binding", user_id)
    if blocked(key):
        raise ValueError("Забагато спроб. Зачекайте 15 хвилин.")
    if group_id >= 0 or len(code) != 15 or not code.isascii() or not code.isdigit():
        failed(key)
        raise ValueError("Потрібна група та 15-значний код.")
    server = db.session.scalar(
        db.select(Server)
        .where(
            Server.authorization_code_hash == code_hash(code),
            Server.authorization_expires_at > utcnow(),
        )
        .with_for_update()
    )
    if not server or (server.telegram_group_id and server.telegram_group_id != group_id):
        db.session.rollback()
        failed(key)
        raise ValueError("Код недійсний, прострочений або призначений іншій групі.")
    if server.ssh_public_key and server.installation_status not in ("READY", "ACTIVE"):
        db.session.rollback()
        raise ValueError("SSH ще не підтверджений або сервер вимкнений.")
    result = db.session.execute(
        update(Server)
        .where(
            Server.id == server.id,
            Server.authorization_code_hash == code_hash(code),
            Server.authorization_expires_at > utcnow(),
        )
        .values(
            telegram_group_id=group_id,
            is_authorized=True,
            installation_status="ACTIVE",
            telegram_authorized_at=utcnow(),
            authorization_code_hash=None,
            authorization_expires_at=None,
        )
    )
    if result.rowcount != 1:
        db.session.rollback()
        raise ValueError("Код уже використано.")
    audit(user_id, "authorize_group", "Групу підключено", server.id)
    db.session.commit()
    return server
