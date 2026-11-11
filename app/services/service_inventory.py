import json
import re
import shlex
from datetime import timedelta

from sqlalchemy import update

from app.extensions import db
from app.models import Job, Server, ServerCpuRecovery, ServerService, utcnow

from .security import audit
from .ssh_client import SSHFailure, connect, execute

NAMES = {
    "systemd": re.compile(r"[A-Za-z0-9_.@-]{1,90}\.service"),
    "docker": re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}"),
}
STATES = {
    "active": "running",
    "running": "running",
    "inactive": "stopped",
    "exited": "stopped",
    "created": "stopped",
    "failed": "failed",
    "dead": "failed",
    "restarting": "failed",
}


def sync_services(server, details, checked_at):
    inventory = details.get("inventory")
    if not isinstance(inventory, list):
        return
    existing = {
        (item.kind, item.name): item
        for item in db.session.scalars(
            db.select(ServerService).where(ServerService.server_id == server.id)
        )
    }
    explicit = set(details.get("services", {}))
    monitored_states = {}
    seen = set()
    for entry in inventory[:250]:
        if not isinstance(entry, dict):
            continue
        kind, name, state = entry.get("type"), entry.get("name"), entry.get("state")
        if not isinstance(kind, str) or kind not in NAMES or not isinstance(name, str):
            continue
        key = kind, name
        if key in seen or not NAMES[kind].fullmatch(name) or not isinstance(state, str):
            continue
        seen.add(key)
        status = STATES.get(state, "unknown")
        item = existing.get(key)
        if item is None:
            item = ServerService(
                server_id=server.id,
                kind=kind,
                name=name,
                monitored=status == "running" or (kind == "systemd" and name in explicit),
            )
            db.session.add(item)
            audit(
                "monitor", "service_discovered", f"{kind} · {name}", server.id, source="automatic"
            )
        item.status, item.checked_at = status, checked_at
        if item.monitored:
            label = name if kind == "systemd" else "Docker · " + name
            monitored_states[label] = {
                "running": "active",
                "stopped": "inactive",
                "failed": "failed",
                "unknown": "unknown",
            }[status]
    for key, item in existing.items():
        if key not in seen:
            item.status = "unknown"
    details["services"] = monitored_states


def restart_service(server, service, actor):
    if not server.is_active or service.checked_at is None or service.status == "unknown":
        raise ValueError("Потрібна актуальна перевірка сервісу на активному VPS.")
    if service.kind not in NAMES or not NAMES[service.kind].fullmatch(service.name):
        raise ValueError("Некоректний сервіс.")
    now = utcnow()
    lease = now.replace(microsecond=0) + timedelta(minutes=2)
    locked = db.session.execute(
        update(Server)
        .where(
            Server.id == server.id,
            Server.is_active.is_(True),
            db.or_(Server.lease_until.is_(None), Server.lease_until < now),
            ~db.select(Job.id).where(Job.server_id == server.id, Job.status == "running").exists(),
            ~db.select(ServerCpuRecovery.server_id)
            .where(
                ServerCpuRecovery.server_id == server.id,
                ServerCpuRecovery.verify_until.is_not(None),
            )
            .exists(),
        )
        .values(lease_until=lease)
    )
    if not locked.rowcount:
        db.session.rollback()
        raise ValueError("VPS виконує іншу операцію. Спробуйте після її завершення.")
    server_id, label = server.id, f"{service.kind} · {service.name}"
    audit(actor, "service_restart", label, server_id, status="pending")
    db.session.commit()
    success = False
    try:
        with connect(server) as client:
            if server.ssh_public_key:
                response = json.loads(
                    execute(
                        client,
                        "serverwatch-service-restart",
                        timeout=50,
                        limit=2048,
                        input_data=json.dumps({"type": service.kind, "name": service.name}),
                    )
                )
                state = response["state"]
            else:
                name = shlex.quote(service.name)
                if service.kind == "systemd":
                    restart = f"/usr/bin/systemctl restart -- {name}"
                    verify = f"/usr/bin/systemctl show --property=ActiveState --value -- {name}"
                else:
                    restart = f"/usr/bin/docker restart --time 10 -- {name}"
                    verify = f"/usr/bin/docker inspect --format '{{{{.State.Status}}}}' -- {name}"
                execute(client, "/usr/bin/sudo -n " + restart, timeout=35, limit=2048)
                state = execute(client, "/usr/bin/sudo -n " + verify, timeout=8, limit=2048).strip()
        service.status = STATES.get(state, "unknown")
        service.checked_at = utcnow()
        success = service.status == "running"
        if success:
            service.restarted_at = service.checked_at
        result = "Сервіс працює." if success else "Сервіс не перейшов у стан Running."
    except (SSHFailure, ValueError, TypeError, KeyError):
        result = "Перезапуск або перевірка стану не підтверджені. Перевірте SSH та права сервісу."
    audit(
        actor,
        "service_restart",
        f"{label} · {result}",
        server_id,
        status="done" if success else "failed",
    )
    db.session.execute(
        update(Server)
        .where(Server.id == server_id, Server.lease_until == lease)
        .values(lease_until=None, next_check_at=utcnow())
    )
    db.session.commit()
    return success, result
