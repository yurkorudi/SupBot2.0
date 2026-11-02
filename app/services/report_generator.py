from datetime import timedelta
from html import escape

from app.extensions import db
from app.models import ServerLog, ServerMetric, utcnow

from .ai_assistant import comment
from .report_comments import load_level, rotating_comment
from .report_holidays import holiday_greeting


def latest_metric(server_id):
    return db.session.scalar(
        db.select(ServerMetric)
        .where(ServerMetric.server_id == server_id)
        .order_by(ServerMetric.collected_at.desc())
        .limit(1)
    )


def size(value):
    return f"{value / (1024**3):.2f} GB" if value >= 1024**3 else f"{value / (1024**2):.0f} MB"


def escaped_excerpt(value, limit):
    parts, used = [], 0
    for character in value:
        encoded = escape(character)
        if used + len(encoded) > limit:
            break
        parts.append(encoded)
        used += len(encoded)
    return "".join(parts)


def recommendations(server, metric, level):
    if level == "unknown":
        return "Відновіть перевірку сервера й отримайте свіжі метрики перед висновками про ресурси."
    advice = []
    for resource, value, tip in (
        ("cpu", metric.cpu_usage, "Перевірте процеси, що навантажують CPU, та чергу виконання."),
        ("ram", metric.ram_percent, "Перевірте споживання пам’яті процесами. SWAP не замінює RAM."),
        (
            "swap",
            metric.swap_percent,
            "Перевірте тиск на пам’ять і активність SWAP; не збільшуйте його навмання.",
        ),
        (
            "disk",
            metric.disk_percent,
            "Перевірте великі файли та ротацію логів; сплануйте звільнення місця.",
        ),
    ):
        if value > server.thresholds[resource + "_warning"]:
            advice.append(tip)
    if any(
        state not in ("active", "unknown") for state in metric.details.get("services", {}).values()
    ):
        advice.append("Перевірте стан і журнал неактивних systemd-сервісів.")
    return (
        "\n".join(advice)
        if advice
        else "Показники нижче порогів. За цим знімком збільшувати SWAP немає необхідності."
    )


def report(server):
    metric = latest_metric(server.id)
    header = (
        f"📊 <b>ЗВІТ СЕРВЕРА</b>\n{escape(server.name)}\nСтан: {escape(server.status.upper())}\n"
    )
    greeting = holiday_greeting(server)
    if greeting:
        header += "\n" + escaped_excerpt(greeting, 240) + "\n"
    if not metric:
        return header + "\nМетрики ще не зібрано."
    details = metric.details
    load = " / ".join(f"{v:.2f}" for v in details.get("load_average", [0, 0, 0]))
    text = (
        f"{header}\n🖥 CPU: {metric.cpu_usage:.1f}%\n"
        f"🧠 RAM: {metric.ram_percent:.1f}% ({size(metric.ram_used)} / {size(metric.ram_total)})\n"
        f"💾 SWAP: {metric.swap_percent:.1f}% ({size(metric.swap_used)} / {size(metric.swap_total)})\n"
        f"💿 DISK: {metric.disk_percent:.1f}% ({size(metric.disk_used)} / {size(metric.disk_total)})\n"
        f"⚙️ Load Average: {load}\n🕒 Uptime: {details.get('uptime', 0) / 86400:.1f} днів\n"
        f"Дані: {metric.collected_at:%Y-%m-%d %H:%M} UTC\n\n<b>АНАЛІЗ</b>\n"
    )
    if utcnow() - metric.collected_at > timedelta(seconds=server.monitoring_interval * 2):
        text += "⚠️ Дані застаріли; поточний стан ресурсів невідомий.\n"
    for kind, value in (
        ("CPU", metric.cpu_usage),
        ("RAM", metric.ram_percent),
        ("SWAP", metric.swap_percent),
        ("DISK", metric.disk_percent),
    ):
        threshold = server.thresholds[kind.lower() + "_warning"]
        text += f"• {kind}: " + ("потрібна увага.\n" if value > threshold else "достатній запас.\n")
    text += "\n<b>РЕКОМЕНДАЦІЇ</b>\n"
    level = load_level(server, metric)
    text += recommendations(server, metric, level) + "\n"
    text += "\n💬 " + escaped_excerpt(rotating_comment(server, level), 240) + "\n"
    ai = comment(metric)
    if ai:
        text += "\nКоментар локального AI: " + escaped_excerpt(ai, 600) + "\n"
    errors = db.session.scalars(
        db.select(ServerLog)
        .where(
            ServerLog.server_id == server.id,
            ServerLog.created_at >= utcnow() - timedelta(hours=4),
            ServerLog.log_level.in_(["warning", "error", "critical"]),
        )
        .order_by(ServerLog.created_at.desc())
        .limit(12)
    ).all()
    if errors:
        lines = [
            f"[{row.created_at:%H:%M:%S}] {row.service_name} — {row.message}" for row in errors
        ]
        text += "\n<b>⚠️ СИСТЕМНІ ПОМИЛКИ</b>\n<blockquote expandable>"
        text += escaped_excerpt("\n".join(lines), 1400) + "</blockquote>"
    return text
