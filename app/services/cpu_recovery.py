import re
from datetime import timedelta
from html import escape

from app.extensions import db
from app.models import Outbox, ServerCpuRecovery

from .alert_manager import enqueue, setting
from .security import audit
from .ssh_client import SSHFailure, connect, execute

BOOT_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")


def log_recovery(server, message, status, actor=None):
    audit(
        "monitor" if actor is None else actor,
        "cpu_recovery" if actor is None else "server_reboot",
        message,
        server.id,
        source="automatic" if actor is None else "manual",
        status=status,
    )
    return enqueue(server, f"{escape(server.name)} · CPU\n{escape(message)}")


def evaluate_cpu_recovery(server, data, now):
    state = db.session.get(ServerCpuRecovery, server.id)
    if state is None:
        return False, server.monitoring_interval
    boot_id = data["details"].get("boot_id") if data is not None else None
    valid_boot = isinstance(boot_id, str) and BOOT_ID.fullmatch(boot_id)
    if state.verify_until:
        if valid_boot and boot_id != state.boot_id:
            log_recovery(
                server,
                "Перезавантаження підтверджено. SSH і метрики відновлено.",
                "done",
                state.manual_actor_id,
            )
        elif now < state.verify_until:
            return False, min(server.monitoring_interval, 30)
        else:
            log_recovery(
                server,
                "Відновлення SSH і зміна boot ID не підтверджені вчасно.",
                "failed",
                state.manual_actor_id,
            )
        state.verify_until = None
        state.boot_id = None
        state.manual_actor_id = None
    if state.manual_actor_id is not None:
        if not valid_boot:
            log_recovery(
                server,
                "Перезавантаження відхилено. Не вдалося перевірити SSH і boot ID.",
                "failed",
                state.manual_actor_id,
            )
            state.manual_actor_id = None
            return False, server.monitoring_interval
        return start_verification(server, state, now, boot_id, 30)
    enabled = (
        state.enabled
        and server.automation_enabled
        and setting("automation_enabled", 0)
        and server.is_active
        and server.is_authorized
        and server.telegram_group_id
    )
    if not enabled or (state.started_at and now - state.started_at < timedelta(days=1)):
        state.previous_critical_at = state.due_at = None
        state.notification_id = None
        return False, server.monitoring_interval
    interval = min(server.monitoring_interval, 60)
    threshold = server.thresholds.get("cpu_critical", max(95, server.thresholds["cpu_warning"]))
    if not valid_boot or data["cpu_usage"] <= threshold:
        if state.notification_id:
            log_recovery(
                server, "Перезавантаження скасовано. Критичний CPU не підтверджено.", "cancelled"
            )
        state.previous_critical_at = state.due_at = None
        state.notification_id = None
        return False, interval
    previous = state.previous_critical_at
    state.previous_critical_at = now
    if not previous or now - previous > timedelta(seconds=120):
        state.due_at = None
        state.notification_id = None
        return False, interval
    notice = db.session.get(Outbox, state.notification_id) if state.notification_id else None
    if notice is None or notice.group_id != server.telegram_group_id:
        notice = log_recovery(
            server, "Критичний CPU. Перевірятимемо навантаження ще 3 хвилини.", "pending"
        )
        db.session.flush()
        state.notification_id = notice.id
        state.due_at = None
    if notice.sent_at is None:
        return False, interval
    state.due_at = notice.sent_at + timedelta(minutes=3)
    if now < state.due_at:
        return False, interval
    return start_verification(server, state, now, boot_id, min(interval, 30))


def start_verification(server, state, now, boot_id, interval):
    state.boot_id = boot_id
    state.started_at = now
    state.verify_until = now + timedelta(minutes=15)
    state.previous_critical_at = state.due_at = None
    state.notification_id = None
    log_recovery(
        server,
        "Запущено перезавантаження. Очікуємо відновлення SSH.",
        "pending",
        state.manual_actor_id,
    )
    return True, interval


def send_reboot(server):
    state = db.session.get(ServerCpuRecovery, server.id)
    automatic = (
        state and state.enabled and server.automation_enabled and setting("automation_enabled", 0)
    )
    if not (server.is_active and state and (state.manual_actor_id is not None or automatic)):
        if state:
            state.verify_until = state.boot_id = None
        log_recovery(
            server,
            "Перезавантаження скасовано в налаштуваннях.",
            "cancelled",
            state.manual_actor_id if state else None,
        )
        if state:
            state.manual_actor_id = None
        return
    command = (
        "serverwatch-reboot"
        if server.ssh_public_key
        else "/usr/bin/sudo -n /usr/bin/systemctl --no-block reboot"
    )
    try:
        with connect(server) as client:
            execute(client, command, timeout=12, limit=2048)
    except SSHFailure:
        # A disconnect can be the result of reboot itself. Only a new boot ID confirms it.
        log_recovery(
            server,
            "SSH не підтвердив команду. Перевіряємо boot ID без повторної команди.",
            "pending",
            state.manual_actor_id,
        )
