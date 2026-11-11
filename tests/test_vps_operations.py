from contextlib import nullcontext
from datetime import timedelta
from unittest.mock import MagicMock
from zipfile import ZipFile

from app.extensions import db
from app.models import (
    AdminAction,
    Alert,
    Job,
    Outbox,
    ServerBackup,
    ServerCpuRecovery,
    ServerService,
    ServerSwapAutomation,
    ServerTerminalAccess,
    Setting,
    utcnow,
)
from app.services.cpu_recovery import evaluate_cpu_recovery, send_reboot
from app.services.database_backups import claim_backup, run_backup
from app.services.service_inventory import sync_services
from app.services.ssh_client import SSHFailure
from app.services.swap_manager import plan_swap, request_swap
from app.services.terminal import allowed_origin, bridge
from monitoring.remote_collect import discover_services
from monitoring.worker import claim_swap_job, run_swap_job

BOOT = "11111111-2222-3333-4444-555555555555"
NEW_BOOT = "22222222-3333-4444-5555-666666666666"


def enable_automation():
    db.session.add(Setting(key="automation_enabled", value=1))
    db.session.commit()


def process_job():
    job_id = claim_swap_job()
    if job_id is not None:
        run_swap_job(job_id)


def test_cpu_requires_delivery_and_sustained_load_then_verifies_boot(server, sample, monkeypatch):
    enable_automation()
    state = ServerCpuRecovery(server_id=server.id, enabled=True)
    db.session.add(state)
    db.session.commit()
    now = utcnow()
    sample["details"]["boot_id"] = BOOT
    sample["cpu_usage"] = 99
    assert not evaluate_cpu_recovery(server, sample, now)[0]
    assert not evaluate_cpu_recovery(server, sample, now + timedelta(minutes=1))[0]
    assert not evaluate_cpu_recovery(server, sample, now + timedelta(minutes=2))[0]
    notice = db.session.get(Outbox, state.notification_id)
    notice.sent_at = now + timedelta(minutes=2)
    for minute in (3, 4):
        assert not evaluate_cpu_recovery(server, sample, now + timedelta(minutes=minute))[0]
    assert evaluate_cpu_recovery(server, sample, now + timedelta(minutes=5))[0]
    db.session.commit()
    execute = MagicMock(side_effect=SSHFailure("disconnect"))
    monkeypatch.setattr("app.services.cpu_recovery.connect", lambda _: nullcontext(object()))
    monkeypatch.setattr("app.services.cpu_recovery.execute", execute)
    send_reboot(server)
    assert execute.call_count == 1
    assert not evaluate_cpu_recovery(server, None, now + timedelta(minutes=6))[0]
    sample["details"]["boot_id"] = NEW_BOOT
    assert not evaluate_cpu_recovery(server, sample, now + timedelta(minutes=7))[0]
    assert state.verify_until is None
    assert not evaluate_cpu_recovery(server, sample, now + timedelta(hours=2))[0]
    assert db.session.scalar(db.select(AdminAction).where(AdminAction.status == "done"))


def test_cpu_cancels_on_normalization_and_missing_ssh(server, sample):
    enable_automation()
    state = ServerCpuRecovery(server_id=server.id, enabled=True)
    db.session.add(state)
    now = utcnow()
    sample["details"]["boot_id"] = BOOT
    sample["cpu_usage"] = 99
    evaluate_cpu_recovery(server, sample, now)
    evaluate_cpu_recovery(server, sample, now + timedelta(minutes=1))
    assert state.notification_id
    assert not evaluate_cpu_recovery(server, None, now + timedelta(minutes=2))[0]
    assert state.notification_id is None
    evaluate_cpu_recovery(server, sample, now + timedelta(minutes=3))
    evaluate_cpu_recovery(server, sample, now + timedelta(minutes=4))
    sample["cpu_usage"] = 20
    assert not evaluate_cpu_recovery(server, sample, now + timedelta(minutes=5))[0]
    assert state.notification_id is None


def prepare_ram_alert(server, sample, minutes_ago):
    enable_automation()
    server.swap_enabled = True
    sample["details"]["persistent_swap"] = True
    sample["ram_percent"] = 99
    alert = Alert(server_id=server.id, alert_type="ram", severity="critical", message="RAM 99%")
    db.session.add_all([alert, ServerSwapAutomation(server_id=server.id, critical_enabled=True)])
    db.session.flush()
    notice = Outbox(
        group_id=server.telegram_group_id,
        message=alert.message,
        alert_id=alert.id,
        sent_at=utcnow() - timedelta(minutes=minutes_ago),
    )
    db.session.add(notice)
    db.session.commit()
    return alert


def test_ram_schedule_delay_and_telegram_manual_takeover(server, sample, monkeypatch):
    alert = prepare_ram_alert(server, sample, 5)
    plan_swap(server, sample, utcnow())
    db.session.commit()
    process_job()
    job = db.session.scalar(db.select(Job))
    assert job.status == "pending" and job.run_after > utcnow()
    request_swap(alert.id, server.telegram_group_id, 123456, 144)
    monkeypatch.setattr("app.services.swap_manager.connect", lambda _: nullcontext(object()))
    execute = MagicMock(return_value='{"status":"already_active"}')
    monkeypatch.setattr("app.services.swap_manager.execute", execute)
    monkeypatch.setattr("monitoring.worker.collect_server", lambda _: sample)
    server.ssh_public_key = "managed-key"
    db.session.commit()
    process_job()
    assert job.status == "done" and job.source == "telegram"
    assert execute.call_args.args[1] == "serverwatch-swap-persistent"
    assert db.session.scalar(db.select(Outbox).where(Outbox.edit_message_id == 144))


def test_ram_automation_uses_fresh_metrics_and_cancels(server, sample, monkeypatch):
    prepare_ram_alert(server, sample, 11)
    plan_swap(server, sample, utcnow())
    db.session.commit()
    sample["ram_percent"] = 30
    monkeypatch.setattr("monitoring.worker.collect_server", lambda _: sample)
    connect = MagicMock()
    monkeypatch.setattr("app.services.swap_manager.connect", connect)
    process_job()
    assert db.session.scalar(db.select(Job)).status == "cancelled"
    connect.assert_not_called()


def test_ram_automation_applies_persistent_helper_when_due(server, sample, monkeypatch):
    prepare_ram_alert(server, sample, 11)
    plan_swap(server, sample, utcnow())
    db.session.commit()
    monkeypatch.setattr("monitoring.worker.collect_server", lambda _: sample)
    monkeypatch.setattr("app.services.swap_manager.connect", lambda _: nullcontext(object()))
    execute = MagicMock(return_value='{"status":"created"}')
    monkeypatch.setattr("app.services.swap_manager.execute", execute)
    process_job()
    process_job()
    assert db.session.scalar(db.select(Job)).status == "done"
    assert execute.call_count == 1


def test_initial_swap_provisions_once_for_a_vps_with_insufficient_swap(server, sample, monkeypatch):
    enable_automation()
    server.swap_enabled = True
    sample["details"]["persistent_swap"] = True
    db.session.add(ServerSwapAutomation(server_id=server.id, initial_enabled=True))
    db.session.commit()
    plan_swap(server, sample, utcnow())
    db.session.commit()
    plan_swap(server, sample, utcnow())
    db.session.commit()
    assert db.session.scalar(db.select(db.func.count(Job.id))) == 1
    monkeypatch.setattr("monitoring.worker.collect_server", lambda _: sample)
    monkeypatch.setattr("app.services.swap_manager.connect", lambda _: nullcontext(object()))
    execute = MagicMock(return_value='{"status":"created"}')
    monkeypatch.setattr("app.services.swap_manager.execute", execute)
    process_job()
    job = db.session.scalar(db.select(Job))
    assert job.status == "done" and job.alert_id is None
    assert execute.call_count == 1


def test_discovery_persists_services_and_respects_disabled_monitoring(client, server, monkeypatch):
    def command(args):
        if "list-unit-files" in args:
            return "nginx.service enabled\nredis.service disabled\n"
        if "list-units" in args:
            return "nginx.service loaded active running\nsystemd-journald.service loaded active running"
        return "postgres\trunning"

    monkeypatch.setattr("monitoring.remote_collect.command", command)
    monkeypatch.setattr("monitoring.remote_collect.shutil.which", lambda _: True)
    details = {"inventory": discover_services({})}
    sync_services(server, details, utcnow())
    db.session.commit()
    nginx = db.session.scalar(db.select(ServerService).where(ServerService.name == "nginx.service"))
    assert details["services"] == {"nginx.service": "active", "Docker · postgres": "active"}
    assert db.session.scalar(db.select(ServerService).where(ServerService.name == "redis.service"))
    response = client.post(
        f"/servers/{server.id}/services/{nginx.id}/monitoring", data={"enabled": "no"}
    )
    assert response.status_code == 302
    sync_services(server, details, utcnow())
    assert "nginx.service" not in details["services"]


def test_restart_checks_scope_locks_and_verified_status(client, server, monkeypatch):
    item = ServerService(
        server_id=server.id,
        kind="docker",
        name="postgres",
        status="stopped",
        monitored=True,
        checked_at=utcnow(),
    )
    db.session.add(item)
    db.session.commit()
    server.ssh_public_key = "managed-key"
    execute = MagicMock(return_value='{"state":"running"}')
    monkeypatch.setattr("app.services.service_inventory.connect", lambda _: nullcontext(object()))
    monkeypatch.setattr("app.services.service_inventory.execute", execute)
    prefix = f"/servers/{server.id}/services/{item.id}/restart"
    assert client.post(prefix).status_code == 400
    server.lease_until = utcnow() + timedelta(minutes=1)
    db.session.commit()
    assert client.post(prefix, data={"confirmed": "yes"}).status_code == 302
    execute.assert_not_called()
    server.lease_until = None
    db.session.commit()
    assert client.post(prefix, data={"confirmed": "yes"}).status_code == 302
    assert item.status == "running" and item.restarted_at
    assert execute.call_args.args[1] == "serverwatch-service-restart"
    assert server.lease_until is None


def test_backup_rotation_keeps_previous_when_dump_fails(app, client, server, tmp_path, monkeypatch):
    app.instance_path = str(tmp_path)
    server_id = server.id
    path = f"/servers/{server_id}/backups"
    monkeypatch.setattr("app.services.database_backups.connect", lambda _: nullcontext(object()))
    monkeypatch.setattr(
        "app.services.database_backups.execute", lambda *args, **kwargs: '["postgresql"]'
    )
    stream = MagicMock(
        side_effect=lambda client, cmd, target, **opts: target.write(
            b"-- PostgreSQL database dump\n" + b"CREATE TABLE sample (id integer);\n" * 4
        )
    )
    monkeypatch.setattr("app.services.database_backups.stream_to", stream)
    assert client.post(path).status_code == 302
    claim = claim_backup()
    assert claim and claim[0] == server_id
    run_backup(app, *claim)
    db.session.expire_all()
    state = db.session.get(ServerBackup, server_id)
    first = state.artifact
    folder = tmp_path / "vps-backups" / str(server_id)
    with ZipFile(folder / first) as archive:
        assert archive.testzip() is None
    stream.side_effect = SSHFailure("dump failed")
    assert client.post(path).status_code == 302
    run_backup(app, *claim_backup())
    db.session.expire_all()
    assert state.status == "failed" and state.artifact == first and (folder / first).exists()
    stream.side_effect = lambda client, cmd, target, **opts: target.write(b"-- " + b"SQL" * 50)
    assert client.post(path).status_code == 302
    run_backup(app, *claim_backup())
    db.session.expire_all()
    assert state.status == "done" and state.artifact != first
    assert [entry.name for entry in folder.iterdir()] == [state.artifact]


def test_mariadb_sandbox_preamble_is_accepted(app, client, server, tmp_path, monkeypatch):
    app.instance_path = str(tmp_path)
    monkeypatch.setattr("app.services.database_backups.connect", lambda _: nullcontext(object()))
    monkeypatch.setattr(
        "app.services.database_backups.execute", lambda *args, **kwargs: '["mariadb"]'
    )
    monkeypatch.setattr(
        "app.services.database_backups.stream_to",
        lambda client, command, target, **options: target.write(
            b"/*M!999999\\- enable the sandbox mode */\n-- MariaDB dump\n"
            + b"CREATE TABLE t(id INT);\n"
        ),
    )
    assert client.post(f"/servers/{server.id}/backups").status_code == 302
    run_backup(app, *claim_backup())
    db.session.expire_all()
    assert db.session.get(ServerBackup, server.id).status == "done"


def test_terminal_origin_and_pty_bridge():
    url = "https://serverwatch.example"
    assert allowed_origin(url, url, "serverwatch.example", True)
    assert not allowed_origin("https://evil.example", url, "serverwatch.example", True)
    assert not allowed_origin(None, url, "serverwatch.example", True)
    ws = MagicMock()
    channel = MagicMock()
    channel.recv_ready.side_effect = [True, False, False, False]
    channel.recv_stderr_ready.return_value = False
    channel.recv.return_value = b"shell$ "
    channel.exit_status_ready.side_effect = [False, False, True]
    ws.receive.side_effect = [b"\x03", '{"cols":100,"rows":30}']
    assert "завершена" in bridge(ws, channel, lambda: True)
    ws.send.assert_called_once_with(b"shell$ ")
    channel.sendall.assert_called_once_with(b"\x03")
    channel.resize_pty.assert_called_once_with(width=100, height=30)


def test_terminal_setup_reuses_host_and_keeps_private_key_out_of_page(client, server, user):
    client.application.config["SESSION_PROTECTION"] = None
    url = f"/servers/{server.id}/terminal"
    assert client.post(url + "/setup", data={"username": "root"}).status_code == 302
    assert not db.session.get(ServerTerminalAccess, server.id)
    assert client.post(url + "/setup", data={"username": "terminal"}).status_code == 302
    access = db.session.get(ServerTerminalAccess, server.id)
    page = client.get(url).get_data(as_text=True)
    assert access.owner_id == user.id
    assert access.ssh_public_key in page and access.ssh_private_key_encrypted not in page
