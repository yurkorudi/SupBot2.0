import json

from flask import current_app

from app.extensions import db
from app.models import Alert, Job, Server, utcnow

from .alert_manager import enqueue
from .security import audit, require_admin
from .ssh_client import SSHFailure, connect, execute

COMMAND = "sudo -n /usr/local/sbin/serverwatch-swap"
SAFE_REASONS = {
    "Потрібен root та запуск без аргументів.",
    "Небезпечний каталог SWAP.",
    "Інша операція SWAP уже виконується.",
    "Файл уже існує. Перезапис заборонено; потрібна ручна перевірка.",
    "Недостатньо місця: потрібен 1 GB та запас 512 MB.",
    "ОС відхилила SWAP. Файл збережено для перевірки; повторів немає.",
    "ОС не підтвердила активний SWAP.",
    "Помилка файлової системи або таймаут. Потрібна ручна перевірка.",
}


def checked_alert(alert_id, group_id):
    alert = db.session.get(Alert, alert_id)
    server = db.session.get(Server, alert.server_id) if alert else None
    if not server or not server.is_authorized or server.telegram_group_id != group_id:
        raise PermissionError("Аварія не належить цій групі.")
    if alert.resolved_at or alert.severity != "critical":
        raise ValueError("Аварію вже закрито або вона не критична.")
    return alert, server


def acknowledge(alert_id, group_id, user_id):
    require_admin(user_id)
    alert, server = checked_alert(alert_id, group_id)
    alert.status = "acknowledged"
    alert.acknowledged_by = user_id
    alert.acknowledged_at = utcnow()
    audit(user_id, "acknowledge", "Аварію взято в роботу", server.id)
    db.session.commit()


def request_swap(alert_id, group_id, user_id):
    require_admin(user_id)
    alert, server = checked_alert(alert_id, group_id)
    db.session.execute(db.select(Server.id).where(Server.id == server.id).with_for_update())
    if not server.is_active or server.status == "offline":
        raise ValueError("Сервер недоступний або моніторинг вимкнено.")
    existing = db.session.scalar(
        db.select(Job.id).where(Job.server_id == server.id, Job.status.in_(["pending", "running"]))
    )
    duplicate = db.session.scalar(db.select(Job.id).where(Job.alert_id == alert.id))
    if existing or duplicate:
        raise ValueError("Операція вже запитана. Повторне створення за цією аварією заборонено.")
    db.session.add(Job(server_id=server.id, alert_id=alert.id, requested_by=user_id))
    audit(user_id, "swap_requested", "Запит SWAP +1 GB", server.id)
    db.session.commit()


def execute_swap(job, server):
    try:
        with connect(server) as client:
            original_timeout = current_app.config["SSH_TIMEOUT"]
            response = json.loads(execute(client, COMMAND, timeout=max(original_timeout, 120)))
        if not isinstance(response, dict):
            raise ValueError
        success = response.get("status") in ("created", "already_active")
        job.status = "done" if success else "failed"
        job.result = (
            "SWAP +1 GB активний до перезавантаження."
            if success
            else (
                response.get("reason")
                if response.get("reason") in SAFE_REASONS
                else "Операцію відхилено. Перевірте helper на сервері."
            )
        )
    except (SSHFailure, ValueError):
        job.status = "failed"
        job.result = (
            "Не вдалося підтвердити SWAP. Перевірте helper і SSH; автоматичного повтору немає."
        )
    job.finished_at = utcnow()
    audit(job.requested_by, "swap_result", job.result, server.id)
    enqueue(server, job.result)
