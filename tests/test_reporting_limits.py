from unittest.mock import patch

import pytest

from app.extensions import db
from app.models import ServerLog, ServerMetric
from app.services.report_comments import PHRASES
from app.services.report_generator import escaped_excerpt, report
from app.services.report_holidays import HOLIDAYS


@pytest.mark.parametrize("overloaded", [False, True])
def test_report_limits_html_without_splitting_entities(server, sample, overloaded):
    server.name = "&" * 100
    if overloaded:
        sample = {
            **sample,
            "cpu_usage": 99,
            "ram_percent": 99,
            "disk_percent": 99,
            "swap_percent": 99,
            "details": {**sample["details"], "services": {"nginx.service": "failed"}},
        }
    db.session.add(ServerMetric(server_id=server.id, **sample))
    for _ in range(12):
        db.session.add(
            ServerLog(
                server_id=server.id, service_name="&" * 100, message="<" * 300, log_level="error"
            )
        )
    db.session.commit()
    longest = max((phrase for phrases in PHRASES.values() for phrase in phrases), key=len)
    with (
        patch("app.services.report_generator.comment", return_value="&" * 400),
        patch("app.services.report_generator.rotating_comment", return_value=longest),
        patch(
            "app.services.report_generator.holiday_greeting",
            return_value=max(HOLIDAYS.values(), key=len),
        ),
    ):
        text = report(server)
    assert len(text) < 4096
    assert text.endswith("</blockquote>")
    assert escaped_excerpt("&&", 7) == "&amp;"
