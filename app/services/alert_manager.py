from datetime import timedelta
from html import escape

from app.extensions import db
from app.models import Alert, Outbox, Setting, utcnow


def setting(key, default):
    item = db.session.get(Setting, key)
    return item.value if item else default


def enqueue(server, message, alert=None):
    if server.is_authorized and server.telegram_group_id:
        db.session.add(
            Outbox(
                group_id=server.telegram_group_id,
                message=message,
                alert_id=alert.id if alert else None,
            )
        )


def update_alert(server, kind, severity, message):
    now = utcnow()
    alert = db.session.scalar(
        db.select(Alert)
        .where(Alert.server_id == server.id, Alert.alert_type == kind, Alert.resolved_at.is_(None))
        .with_for_update()
    )
    if severity is None:
        if alert:
            alert.status, alert.resolved_at = "resolved", now
            enqueue(server, f"✅ Показники нормалізувалися: {escape(server.name)} · {kind.upper()}")
        return
    if not alert:
        alert = Alert(server_id=server.id, alert_type=kind, severity=severity, message=message)
        db.session.add(alert)
        db.session.flush()
        enqueue(server, message, alert if severity == "critical" else None)
        return
    escalated = severity == "critical" and alert.severity != "critical"
    alert.message = message
    alert.severity = severity
    if escalated or (now - alert.notified_at >= timedelta(seconds=setting("alert_repeat", 3600))):
        enqueue(server, message, alert if severity == "critical" else None)
        alert.notified_at = now


def evaluate(server, metric):
    thresholds = server.thresholds
    warning = False
    summary = (
        f"CPU: {metric.cpu_usage:.1f}%\nRAM: {metric.ram_percent:.1f}%\n"
        f"SWAP: {metric.swap_percent:.1f}%\nDISK: {metric.disk_percent:.1f}%"
    )
    for kind, value in (
        ("cpu", metric.cpu_usage),
        ("ram", metric.ram_percent),
        ("disk", metric.disk_percent),
        ("swap", metric.swap_percent),
    ):
        severity = None
        if value > thresholds.get(kind + "_critical", 101):
            severity = "critical"
        elif value > thresholds[kind + "_warning"]:
            severity = "warning"
        warning |= severity is not None
        label = "🚨 КРИТИЧНЕ ПОПЕРЕДЖЕННЯ" if severity == "critical" else "⚠️ ПОПЕРЕДЖЕННЯ"
        message = (
            f"<b>{label}</b>\n\nСервер: {escape(server.name)}\n\n{summary}\n\n"
            f"{kind.upper()}: перевищено поріг. Потрібна увага адміністратора.\n@Yureweo"
        )
        update_alert(server, kind, severity, message)
    for name, state in metric.details.get("services", {}).items():
        if state not in ("active", "unknown"):
            warning = True
    service_problem = any(
        state not in ("active", "unknown") for state in metric.details.get("services", {}).values()
    )
    update_alert(
        server,
        "service",
        "warning" if service_problem else None,
        f"⚠️ {escape(server.name)}: вибраний systemd-сервіс неактивний. @Yureweo",
    )
    server.status = "warning" if warning else "online"


def connection_failed(server, reason):
    server.status = "offline"
    server.last_error = reason
    last = str(server.last_success_at) if server.last_success_at else "немає успішних перевірок"
    update_alert(
        server,
        "connection",
        "critical",
        f"🔴 <b>СЕРВЕР НЕДОСТУПНИЙ</b>\n\n{escape(server.name)}\n"
        f"{escape(reason)}\nОстання успішна перевірка: {last} UTC\n"
        "Причину стану ресурсів неможливо підтвердити без SSH.\n@Yureweo",
    )
