from datetime import datetime, timedelta

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Outbox, ServerReportSchedule, utcnow
from app.services.report_schedule import enqueue_due_reports, next_slot, save_schedule
from monitoring.worker import monitor_one


def test_local_schedule_handles_both_dst_transitions():
    schedule = ServerReportSchedule(timezone="Europe/Berlin", weekdays=[6], times=["02:30"])
    assert next_slot(schedule, datetime(2026, 3, 29)) == datetime(2026, 4, 5, 0, 30)
    assert next_slot(schedule, datetime(2026, 10, 25)) == datetime(2026, 10, 25, 0, 30)
    assert next_slot(schedule, datetime(2026, 10, 25, 0, 30)) == datetime(2026, 11, 1, 1, 30)


def test_schedule_advances_transactionally_and_coalesces_missed_reports(server):
    save_schedule(
        server,
        MultiDict(
            {
                "report_mode": "schedule",
                "report_timezone": "Europe/Kyiv",
                "report_times": "21:00, 09:00, 09:00",
                "report_weekdays": ["0", "4"],
            }
        ),
    )
    db.session.commit()
    schedule = db.session.get(ServerReportSchedule, server.id)
    assert schedule.times == ["09:00", "21:00"]
    assert schedule.weekdays == [0, 4]
    schedule.next_at = utcnow() - timedelta(days=2)
    db.session.commit()
    server_id = server.id
    enqueue_due_reports()
    db.session.remove()
    enqueue_due_reports()
    assert db.session.scalar(db.select(db.func.count(Outbox.id))) == 1
    assert db.session.get(ServerReportSchedule, server_id).next_at > utcnow()


def test_disabled_schedule_still_allows_manual_report(server, sample, monkeypatch):
    save_schedule(server, MultiDict({"report_mode": "disabled"}))
    db.session.commit()
    monkeypatch.setattr("monitoring.worker.collect_server", lambda _: sample)
    monitor_one(server.id)
    enqueue_due_reports()
    assert db.session.scalar(db.select(db.func.count(Outbox.id))) == 0
    schedule = db.session.get(ServerReportSchedule, server.id)
    schedule.manual_requested_at = utcnow()
    db.session.commit()
    monitor_one(server.id)
    monitor_one(server.id)
    assert db.session.scalar(db.select(db.func.count(Outbox.id))) == 1
    assert schedule.manual_requested_at is None
