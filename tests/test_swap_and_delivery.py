from datetime import timedelta
from unittest.mock import patch

import pytest

from app.extensions import db
from app.models import Alert, Job, Outbox, utcnow
from app.services.swap_manager import acknowledge, execute_swap, request_swap
from bot.main import claim_messages, mark_message


@pytest.fixture
def alert(server):
    server.status = "warning"
    alert = Alert(server_id=server.id, alert_type="ram", severity="critical", message="RAM 95%")
    db.session.add(alert)
    db.session.commit()
    return alert


def test_admin_acknowledgement(server, alert):
    with pytest.raises(PermissionError):
        acknowledge(alert.id, server.telegram_group_id, 888)
    acknowledge(alert.id, server.telegram_group_id, 123456)
    assert alert.status == "acknowledged"
    assert alert.acknowledged_by == 123456 and alert.acknowledged_at


def test_swap_authorization_and_duplicate(server, alert):
    with pytest.raises(PermissionError):
        request_swap(alert.id, server.telegram_group_id, 777)
    with pytest.raises(PermissionError):
        request_swap(alert.id, -200, 123456)
    request_swap(alert.id, server.telegram_group_id, 123456)
    with pytest.raises(ValueError):
        request_swap(alert.id, server.telegram_group_id, 123456)
    assert db.session.scalar(db.select(db.func.count(Job.id))) == 1


def test_swap_offline_refused(server, alert):
    server.status = "offline"
    db.session.commit()
    with pytest.raises(ValueError):
        request_swap(alert.id, server.telegram_group_id, 123456)


def test_swap_uses_only_fixed_command(server, alert):
    from contextlib import nullcontext

    request_swap(alert.id, server.telegram_group_id, 123456)
    job = db.session.scalar(db.select(Job))
    with (
        patch("app.services.swap_manager.connect", return_value=nullcontext(object())),
        patch("app.services.swap_manager.execute", return_value='{"status":"created"}') as execute,
    ):
        execute_swap(job, server)
    assert execute.call_args.args[1] == "sudo -n /usr/local/sbin/serverwatch-swap"
    assert job.status == "done" and job.finished_at


def test_outbox_claim_retry_success(server):
    item = Outbox(group_id=server.telegram_group_id, message="test")
    db.session.add(item)
    db.session.commit()
    assert len(claim_messages()) == 1
    assert claim_messages() == []
    mark_message(item.id, False, -1)
    assert len(claim_messages()) == 1
    mark_message(item.id, True)
    assert claim_messages() == []


def test_multiple_worker_claim_is_exclusive(server):
    from monitoring.worker import claim_server

    assert claim_server(server.id)
    assert not claim_server(server.id)
    server.lease_until = utcnow() - timedelta(seconds=1)
    db.session.commit()
    assert claim_server(server.id)
