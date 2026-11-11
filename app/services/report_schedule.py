import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import update

from app.extensions import db
from app.models import Server, ServerReportSchedule, utcnow

from .alert_manager import enqueue
from .report_generator import report


def next_slot(schedule, after):
    zone = ZoneInfo(schedule.timezone)
    local = after.replace(tzinfo=timezone.utc).astimezone(zone)
    # A spring-forward gap skips that day's slot. A fall-back slot uses its first occurrence.
    for offset in range(15):
        day = local.date() + timedelta(days=offset)
        if day.weekday() not in schedule.weekdays:
            continue
        for value in schedule.times:
            wall = datetime.combine(day, time.fromisoformat(value))
            candidate = wall.replace(tzinfo=zone).astimezone(timezone.utc)
            if (
                candidate.astimezone(zone).replace(tzinfo=None) == wall
                and candidate.replace(tzinfo=None) > after
            ):
                return candidate.replace(tzinfo=None)
    raise ValueError("Не вдалося визначити наступний час звіту.")


def save_schedule(server, form):
    mode = form.get("report_mode")
    if mode not in ("interval", "schedule", "disabled"):
        raise ValueError("Виберіть режим надсилання звітів.")
    zone = form.get("report_timezone", "").strip() or "UTC"
    try:
        if len(zone) > 64:
            raise ValueError
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Вкажіть часовий пояс IANA, наприклад Europe/Kyiv.") from None
    times = sorted(set(form.get("report_times", "").replace(",", " ").split()))
    if len(times) > 24 or any(
        not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", v) for v in times
    ):
        raise ValueError("Вкажіть до 24 значень часу у форматі ГГ:ХХ.")
    days = set(form.getlist("report_weekdays"))
    if days.difference("0123456") or (mode == "schedule" and not (times and days)):
        raise ValueError("Для графіка виберіть дні тижня та час.")
    schedule = db.session.get(ServerReportSchedule, server.id)
    if schedule is None:
        schedule = ServerReportSchedule(server_id=server.id)
        db.session.add(schedule)
    schedule.mode, schedule.timezone = mode, zone
    schedule.times, schedule.weekdays = times, sorted(map(int, days))
    schedule.next_at = next_slot(schedule, utcnow()) if mode == "schedule" else None


def enqueue_due_reports():
    now = utcnow()
    due = db.session.execute(
        db.select(ServerReportSchedule.server_id, ServerReportSchedule.next_at)
        .where(ServerReportSchedule.next_at <= now)
        .order_by(ServerReportSchedule.next_at)
        .limit(10)
    ).all()
    for server_id, due_at in due:
        schedule = db.session.get(ServerReportSchedule, server_id)
        if schedule is None or schedule.next_at != due_at:
            continue
        advanced = db.session.execute(
            update(ServerReportSchedule)
            .where(
                ServerReportSchedule.server_id == server_id, ServerReportSchedule.next_at == due_at
            )
            .values(next_at=next_slot(schedule, now))
        )
        if advanced.rowcount:
            server = db.session.get(Server, server_id)
            if server.is_active and server.is_authorized and server.telegram_group_id:
                enqueue(server, report(server))
        db.session.commit()


def enqueue_manual_report(server, schedule):
    requested_at = schedule.manual_requested_at
    if requested_at is None:
        return
    consumed = db.session.execute(
        update(ServerReportSchedule)
        .where(
            ServerReportSchedule.server_id == server.id,
            ServerReportSchedule.manual_requested_at == requested_at,
        )
        .values(manual_requested_at=None)
    )
    if consumed.rowcount and server.is_authorized and server.telegram_group_id:
        enqueue(server, report(server))
