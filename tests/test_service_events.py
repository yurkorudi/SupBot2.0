import json

import pytest

from app.extensions import db
from app.models import ServerLog, ServerMetric, utcnow
from app.services.report_generator import report
from monitoring.remote_collect import parse_service_event


def journal(unit, message, priority=6):
    return json.dumps(
        {
            "UNIT": unit,
            "MESSAGE": message,
            "PRIORITY": str(priority),
            "__REALTIME_TIMESTAMP": "1800000000000000",
        }
    )


@pytest.mark.parametrize(
    "unit",
    ["user@1000.service", "user-runtime-dir@1000.service", "user@0.service", "fwupd.service"],
)
def test_ordinary_unmonitored_unit_changes_are_ignored(unit):
    assert parse_service_event(journal(unit, "Started Session service"), ["nginx.service"]) is None


def test_monitored_restart_stays_in_history_as_notice():
    event = parse_service_event(
        journal("nginx.service", "Scheduled restart job"), ["nginx.service"]
    )
    assert event["level"] == "notice"
    assert event["message"] == "Перезапуск сервісу"


@pytest.mark.parametrize("unit", ["nginx.service", "user@1000.service"])
def test_real_failures_are_kept_even_for_unselected_units(unit):
    event = parse_service_event(
        journal(unit, "Failed with result exit-code; secret=do-not-export"), []
    )
    assert event["level"] == "error"
    assert "secret" not in event["message"]


def test_warning_from_service_is_kept_without_raw_message():
    value = json.dumps(
        {
            "_SYSTEMD_UNIT": "mysql.service",
            "PRIORITY": "4",
            "MESSAGE": "private connection details",
            "__REALTIME_TIMESTAMP": "1800000000000000",
        }
    )
    event = parse_service_event(value, [])
    assert event["level"] == "warning"
    assert "private" not in event["message"]


def test_report_excludes_existing_notices_before_applying_limit(server, sample):
    db.session.add(ServerMetric(server_id=server.id, **sample))
    db.session.add(
        ServerLog(
            server_id=server.id,
            service_name="nginx.service",
            log_level="error",
            message="Помилка системного сервісу",
        )
    )
    db.session.flush()
    for _ in range(20):
        db.session.add(
            ServerLog(
                server_id=server.id,
                service_name="user@1000.service",
                log_level="notice",
                message="Зміна стану сервісу",
            )
        )
    db.session.commit()
    text = report(server)
    assert "nginx.service" in text
    assert "user@1000.service" not in text
    assert "СИСТЕМНІ ПОМИЛКИ" in text


def test_report_omits_error_section_for_notices_only(server, sample):
    db.session.add(ServerMetric(server_id=server.id, **sample))
    db.session.add(
        ServerLog(
            server_id=server.id,
            service_name="user@0.service",
            log_level="notice",
            message="Зміна стану сервісу",
            created_at=utcnow(),
        )
    )
    db.session.commit()
    assert "СИСТЕМНІ ПОМИЛКИ" not in report(server)
