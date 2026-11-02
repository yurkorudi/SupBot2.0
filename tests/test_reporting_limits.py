from unittest.mock import patch

from app.extensions import db
from app.models import ServerLog, ServerMetric
from app.services.report_generator import escaped_excerpt, report


def test_report_limits_html_without_splitting_entities(server, sample):
    server.name = "&" * 100
    db.session.add(ServerMetric(server_id=server.id, **sample))
    for _ in range(12):
        db.session.add(
            ServerLog(
                server_id=server.id, service_name="&" * 100, message="<" * 300, log_level="error"
            )
        )
    db.session.commit()
    with patch("app.services.report_generator.comment", return_value="&" * 400):
        text = report(server)
    assert len(text) < 4096
    assert text.endswith("</blockquote>")
    assert escaped_excerpt("&&", 7) == "&amp;"
