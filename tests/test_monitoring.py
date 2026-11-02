from datetime import timedelta
from unittest.mock import patch

from app.extensions import db
from app.models import Alert, HourlyMetric, Outbox, ServerMetric, utcnow
from app.services.alert_manager import evaluate
from app.services.report_generator import report
from app.services.ssh_client import SSHFailure
from monitoring.cleanup import cleanup
from monitoring.worker import monitor_one


def test_thresholds_repeat_escalation_recovery(server, sample):
    metric = ServerMetric(server_id=server.id, **sample)
    metric.cpu_usage = 81
    metric.ram_percent = 82
    evaluate(server, metric)
    db.session.commit()
    assert db.session.scalar(db.select(db.func.count(Alert.id))) == 2
    assert db.session.scalar(db.select(db.func.count(Outbox.id))) == 2
    evaluate(server, metric)
    db.session.commit()
    assert db.session.scalar(db.select(db.func.count(Outbox.id))) == 2
    metric.ram_percent = 95
    evaluate(server, metric)
    db.session.commit()
    assert db.session.scalar(db.select(db.func.count(Outbox.id))) == 3
    alert = db.session.scalar(db.select(Alert).where(Alert.alert_type == "ram"))
    assert alert.severity == "critical"
    alert.notified_at = utcnow() - timedelta(hours=2)
    evaluate(server, metric)
    db.session.commit()
    assert db.session.scalar(db.select(db.func.count(Outbox.id))) == 4
    metric.cpu_usage, metric.ram_percent = 10, 40
    evaluate(server, metric)
    db.session.commit()
    assert server.status == "online"
    assert all(a.resolved_at for a in db.session.scalars(db.select(Alert)))
    assert db.session.scalar(db.select(db.func.count(Outbox.id))) == 6


def test_exact_threshold_is_not_exceeded(server, sample):
    metric = ServerMetric(server_id=server.id, **sample)
    metric.cpu_usage, metric.ram_percent = 80, 80
    evaluate(server, metric)
    assert db.session.scalar(db.select(db.func.count(Alert.id))) == 0


def test_cleanup_never_overwrites_full_hour_with_partial_raw(server, sample):
    from monitoring.cleanup import aggregate_hour

    stamp = (utcnow() - timedelta(hours=25)).replace(minute=0, second=0, microsecond=0)
    first = ServerMetric(server_id=server.id, collected_at=stamp, **{**sample, "cpu_usage": 20})
    last = ServerMetric(
        server_id=server.id,
        collected_at=stamp + timedelta(minutes=50),
        **{**sample, "cpu_usage": 80},
    )
    db.session.add_all([first, last])
    db.session.flush()
    aggregate_hour(server.id, stamp)
    db.session.commit()
    db.session.delete(first)
    db.session.commit()
    cleanup()
    hourly = db.session.scalar(db.select(HourlyMetric).where(HourlyMetric.hour == stamp))
    assert hourly.cpu_avg == 50 and hourly.cpu_max == 80


def test_offline_retry_and_recovery(server, sample):
    with (
        patch(
            "monitoring.worker.collect_server",
            side_effect=SSHFailure("Час очікування SSH вичерпано."),
        ) as collect,
        patch("monitoring.worker.time.sleep"),
    ):
        monitor_one(server.id)
        assert collect.call_count == 2
    assert server.status == "offline"
    assert "RAM" not in server.last_error
    alert = db.session.scalar(db.select(Alert))
    assert alert.alert_type == "connection"
    with patch("monitoring.worker.collect_server", return_value=sample):
        monitor_one(server.id)
    assert server.status == "online"
    assert alert.resolved_at
    assert db.session.scalar(db.select(ServerMetric.id))


def test_cleanup_preserves_hourly_history(server, sample):
    stamp = (utcnow() - timedelta(hours=25)).replace(minute=0, second=0, microsecond=0)
    for cpu in (20, 60):
        data = {**sample, "cpu_usage": cpu}
        db.session.add(ServerMetric(server_id=server.id, collected_at=stamp, **data))
    db.session.add(ServerMetric(server_id=server.id, collected_at=utcnow(), **sample))
    db.session.commit()
    from monitoring.cleanup import aggregate_hour

    aggregate_hour(server.id, utcnow().replace(minute=0, second=0, microsecond=0))
    db.session.commit()
    cleanup()
    old = db.session.scalar(db.select(HourlyMetric).where(HourlyMetric.hour == stamp))
    assert old.cpu_avg == 40 and old.cpu_max == 60
    assert db.session.scalar(db.select(db.func.count(ServerMetric.id))) == 1
    cleanup()
    assert db.session.scalar(db.select(db.func.count(HourlyMetric.id))) == 2


def test_report_escapes_name_and_summarizes_errors(server, sample):
    from app.models import ServerLog

    db.session.add(ServerMetric(server_id=server.id, **sample))
    db.session.add(
        ServerLog(
            server_id=server.id,
            service_name="nginx.service",
            log_level="error",
            message="Системна помилка",
        )
    )
    db.session.commit()
    text = report(server)
    assert "Production &lt;01&gt;" in text
    assert "<blockquote expandable>" in text
    assert len(text) < 4096


def test_ai_unavailable_falls_back(app, server, sample):
    app.config["AI_ENABLED"] = True
    db.session.add(ServerMetric(server_id=server.id, **sample))
    db.session.commit()
    with patch("app.services.ai_assistant.urlopen", side_effect=OSError):
        assert "РЕКОМЕНДАЦІЇ" in report(server)


def test_analytics_periods(client, server, sample):
    db.session.add(ServerMetric(server_id=server.id, **sample))
    db.session.commit()
    from monitoring.cleanup import aggregate_hour

    aggregate_hour(server.id, utcnow().replace(minute=0, second=0, microsecond=0))
    db.session.commit()
    cleanup()
    for period in ("24h", "7d", "30d"):
        response = client.get(f"/api/analytics/{server.id}?period={period}")
        assert response.status_code == 200
        assert response.json["cpu"] == [30]
    assert client.get(f"/api/analytics/{server.id}?period=all").status_code == 400


def test_all_pages_render(client, server, sample):
    db.session.add(ServerMetric(server_id=server.id, **sample))
    db.session.commit()
    for path in (
        "/",
        "/servers",
        "/servers/add",
        f"/servers/{server.id}",
        f"/servers/{server.id}/edit",
        f"/servers/{server.id}/logs",
        "/alerts",
        "/settings",
        "/security",
    ):
        response = client.get(path)
        assert response.status_code == 200, (path, response.get_data(as_text=True))


def test_add_server_with_mock_ssh(client):
    from contextlib import nullcontext

    from app.models import Server
    from app.services.encryption import decrypt

    fields = {
        "name": "New server",
        "ip_address": "8.8.4.4",
        "ssh_username": "monitor",
        "private_key": "a-private-key",
        "ssh_port": "22",
        "monitoring_interval": "180",
    }
    with (
        patch("app.routes.servers.parse_private_key"),
        patch("app.routes.servers.connect", return_value=nullcontext()),
    ):
        response = client.post("/servers/add", data=fields)
    assert response.status_code == 200
    server = db.session.scalar(db.select(Server).where(Server.name == "New server"))
    assert server.authorization_code_hash
    assert decrypt(server.ssh_private_key_encrypted) == fields["private_key"]
    assert "a-private-key" not in response.get_data(as_text=True)


def test_can_disable_offline_server_without_ssh(client, server):
    server.status = "offline"
    db.session.commit()
    fields = {
        "name": server.name,
        "ip_address": server.ip_address,
        "ssh_username": server.ssh_username,
        "ssh_port": server.ssh_port,
        "monitoring_interval": 180,
        "telegram_group_id": server.telegram_group_id,
    }
    with patch("app.routes.servers.connect") as connect:
        response = client.post(f"/servers/{server.id}/edit", data=fields)
    assert response.status_code == 302
    assert not server.is_active
    connect.assert_not_called()
