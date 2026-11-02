from datetime import timedelta

from sqlalchemy import delete, func

from app.extensions import db
from app.models import HourlyMetric, LoginAttempt, Outbox, ServerLog, ServerMetric, utcnow


def aggregate_hour(server_id, hour, only_missing=False):
    row = db.session.scalar(
        db.select(HourlyMetric).where(
            HourlyMetric.server_id == server_id, HourlyMetric.hour == hour
        )
    )
    # Completed buckets must survive removal of a partial hour of raw samples.
    if row and only_missing:
        return
    end = hour + timedelta(hours=1)
    expressions = []
    for field in (
        ServerMetric.cpu_usage,
        ServerMetric.ram_percent,
        ServerMetric.swap_percent,
        ServerMetric.disk_percent,
    ):
        expressions.extend((func.avg(field), func.max(field)))
    values = db.session.execute(
        db.select(*expressions).where(
            ServerMetric.server_id == server_id,
            ServerMetric.collected_at >= hour,
            ServerMetric.collected_at < end,
        )
    ).one()
    if values[0] is None:
        return
    if not row:
        row = HourlyMetric(server_id=server_id, hour=hour)
        db.session.add(row)
    for key, value in zip(
        (
            "cpu_avg",
            "cpu_max",
            "ram_avg",
            "ram_max",
            "swap_avg",
            "swap_max",
            "disk_avg",
            "disk_max",
        ),
        values,
    ):
        setattr(row, key, value)


def cleanup():
    now = utcnow()
    # Small grouped query; do not stream an open MySQL cursor while issuing other queries.
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    ranges = db.session.execute(
        db.select(
            ServerMetric.server_id,
            func.min(ServerMetric.collected_at),
            func.max(ServerMetric.collected_at),
        )
        .where(ServerMetric.collected_at >= now - timedelta(days=90))
        .group_by(ServerMetric.server_id)
    ).all()
    for server_id, start, end in ranges:
        hour = start.replace(minute=0, second=0, microsecond=0)
        while hour <= end and hour < current_hour:
            aggregate_hour(server_id, hour, only_missing=True)
            hour += timedelta(hours=1)
    db.session.flush()
    db.session.execute(
        delete(ServerMetric).where(ServerMetric.collected_at < now - timedelta(hours=24))
    )
    db.session.execute(delete(HourlyMetric).where(HourlyMetric.hour < now - timedelta(days=90)))
    db.session.execute(delete(ServerLog).where(ServerLog.created_at < now - timedelta(days=90)))
    db.session.execute(delete(Outbox).where(Outbox.sent_at < now - timedelta(days=7)))
    db.session.execute(
        delete(LoginAttempt).where(LoginAttempt.updated_at < now - timedelta(days=1))
    )
    db.session.commit()
