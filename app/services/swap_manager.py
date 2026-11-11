import json
from datetime import timedelta
from html import escape

from flask import current_app

from app.extensions import db
from app.models import Alert, Job, Outbox, Server, ServerSwapAutomation, utcnow

from .alert_manager import enqueue, setting
from .report_generator import latest_metric
from .security import audit, require_admin
from .ssh_client import SSHFailure, connect, execute

COMMAND = "sudo -n /usr/local/sbin/serverwatch-swap"
MIN_SWAP = 2 * 1024**3 - 65536
PERSISTENT_ERRORS = {
    "unsupported_filesystem": "Потрібна файлова система ext4 або XFS.",
    "insufficient_space": "Потрібно 2 GiB для SWAP і щонайменше 512 MiB запасу.",
    "unsafe_existing_file": "Наявний файл SWAP потребує ручної перевірки.",
    "unsafe_unit": "Наявний systemd unit SWAP потребує ручної перевірки.",
    "activation_unconfirmed": "ОС не підтвердила активацію SWAP.",
}
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
    audit(user_id, "acknowledge", "Аварію взято в роботу", server.id, source="telegram")
    db.session.commit()


def request_swap(alert_id, group_id, user_id, message_id=None):
    require_admin(user_id)
    alert, server = checked_alert(alert_id, group_id)
    if alert.alert_type != "ram":
        raise ValueError("SWAP доступний лише для критичного сповіщення RAM.")
    db.session.execute(db.select(Server.id).where(Server.id == server.id).with_for_update())
    if not server.is_active or server.status == "offline":
        raise ValueError("Сервер недоступний або моніторинг вимкнено.")
    if server.ssh_public_key and not server.swap_enabled:
        raise ValueError("SWAP не дозволено під час встановлення цього сервера.")
    existing = db.session.scalar(db.select(Job).where(Job.alert_id == alert.id).with_for_update())
    if existing and existing.source == "automatic" and existing.status == "pending":
        existing.source, existing.requested_by, existing.run_after = "telegram", user_id, None
    elif existing or db.session.scalar(
        db.select(Job.id).where(Job.server_id == server.id, Job.status == "running").limit(1)
    ):
        raise ValueError("Операція вже запитана. Повторне створення за цією аварією заборонено.")
    else:
        existing = Job(server_id=server.id, alert_id=alert.id, requested_by=user_id)
        db.session.add(existing)
    existing.telegram_message_id = message_id
    existing.telegram_group_id = group_id
    audit(user_id, "swap_requested", "Запит SWAP", server.id, source="telegram", status="pending")
    db.session.commit()


def plan_swap(server, data, now):
    policy = db.session.get(ServerSwapAutomation, server.id)
    if not (
        policy
        and server.swap_enabled
        and server.automation_enabled
        and setting("automation_enabled", 0)
    ):
        return False
    db.session.execute(db.select(Server.id).where(Server.id == server.id).with_for_update())
    if policy.initial_enabled and not policy.initial_queued:
        policy.initial_queued = True
        if data["swap_total"] < MIN_SWAP:
            db.session.add(
                Job(server_id=server.id, requested_by=0, source="automatic", run_after=now)
            )
            audit(
                "monitor",
                "swap_requested",
                "Початкова перевірка SWAP",
                server.id,
                source="automatic",
                status="pending",
            )
    if policy.critical_enabled and data["ram_percent"] <= server.thresholds["ram_critical"]:
        pending = db.session.scalars(
            db.select(Job)
            .where(
                Job.server_id == server.id,
                Job.alert_id.is_not(None),
                Job.source == "automatic",
                Job.status == "pending",
            )
            .with_for_update()
        ).all()
        for job in pending:
            finish_swap(job, server, "cancelled", "SWAP скасовано. Показники RAM нормалізувалися.")
    elif policy.critical_enabled:
        alert = db.session.scalar(
            db.select(Alert).where(
                Alert.server_id == server.id,
                Alert.alert_type == "ram",
                Alert.severity == "critical",
                Alert.status == "open",
                Alert.resolved_at.is_(None),
            )
        )
        if alert and not db.session.scalar(
            db.select(Job.id).where(Job.alert_id == alert.id).with_for_update()
        ):
            sent_at = db.session.scalar(
                db.select(Outbox.sent_at)
                .where(
                    Outbox.alert_id == alert.id,
                    Outbox.group_id == server.telegram_group_id,
                    Outbox.sent_at.is_not(None),
                )
                .order_by(Outbox.sent_at)
                .limit(1)
            )
            if sent_at:
                db.session.add(
                    Job(
                        server_id=server.id,
                        alert_id=alert.id,
                        requested_by=0,
                        source="automatic",
                        run_after=sent_at + timedelta(minutes=10),
                    )
                )
                audit(
                    "monitor",
                    "swap_requested",
                    "SWAP після критичного сповіщення RAM",
                    server.id,
                    source="automatic",
                    status="pending",
                )
    return policy.critical_enabled


def validate_automatic_swap(job, server, data):
    policy = db.session.get(ServerSwapAutomation, server.id)
    if not (
        server.is_active
        and server.swap_enabled
        and server.automation_enabled
        and policy
        and setting("automation_enabled", 0)
    ):
        return "cancelled", "SWAP скасовано в налаштуваннях."
    if job.alert_id:
        alert = db.session.get(Alert, job.alert_id)
        if not (
            policy.critical_enabled
            and alert
            and alert.status == "open"
            and alert.severity == "critical"
            and not alert.resolved_at
        ):
            return "cancelled", "Сповіщення RAM закрито або взято в роботу."
    elif not policy.initial_enabled:
        return "cancelled", "Початкове створення SWAP вимкнено."
    if not data:
        return "failed", "Не вдалося перевірити VPS через SSH."
    if job.alert_id and data["ram_percent"] <= server.thresholds["ram_critical"]:
        return "cancelled", "SWAP скасовано. Показники RAM нормалізувалися."
    if data["swap_total"] >= MIN_SWAP:
        return "done", "Активний SWAP уже має потрібний обсяг."
    if not data["details"].get("persistent_swap"):
        return "failed", "VPS не підтвердив наявність helper постійного SWAP."
    return None


def finish_swap(job, server, status, result):
    job.status, job.result, job.finished_at = status, result, utcnow()
    actor = job.requested_by if job.source == "telegram" else "monitor"
    audit(actor, "swap_result", result, server.id, source=job.source, status=status)
    message = f"{escape(server.name)} · SWAP\n{escape(result)}"
    if job.telegram_message_id and job.telegram_group_id == server.telegram_group_id:
        alert = db.session.get(Alert, job.alert_id)
        if alert and len(alert.message) + len(message) < 4090:
            message = f"{alert.message}\n\n{message}"
        enqueue(server, message, edit_message_id=job.telegram_message_id)
    else:
        enqueue(server, message)


def execute_swap(job, server, data=None):
    if job.source == "automatic":
        decision = validate_automatic_swap(job, server, data)
        if decision:
            finish_swap(job, server, *decision)
            return
    metric = latest_metric(server.id) if data is None else None
    details = data["details"] if data is not None else (metric.details if metric else {})
    persistent = bool(details.get("persistent_swap"))
    try:
        with connect(server) as client:
            original_timeout = current_app.config["SSH_TIMEOUT"]
            command = "serverwatch-swap" if server.ssh_public_key else COMMAND
            if persistent:
                command = (
                    "serverwatch-swap-persistent"
                    if server.ssh_public_key
                    else COMMAND + "-persistent"
                )
            response = json.loads(
                execute(client, command, timeout=max(original_timeout, 160 if persistent else 135))
            )
        if not isinstance(response, dict):
            raise ValueError
        success = response.get("status") in ("created", "already_active")
        status = "done" if success else "failed"
        result = (
            (
                "Підтверджено щонайменше 2 GiB SWAP."
                if persistent
                else "SWAP +1 GB активний до перезавантаження."
            )
            if success
            else (
                response.get("reason")
                if response.get("reason") in SAFE_REASONS
                else PERSISTENT_ERRORS.get(
                    response.get("code"), "Операцію відхилено. Перевірте helper на сервері."
                )
            )
        )
    except (SSHFailure, ValueError, TypeError):
        status = "failed"
        result = "Не вдалося підтвердити SWAP. Перевірте helper і SSH; автоматичного повтору немає."
    finish_swap(job, server, status, result)
