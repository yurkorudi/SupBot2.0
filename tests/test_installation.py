import ast
import base64
import io
import json
import re
import shlex
import sys
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import bashlex
import paramiko
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.extensions import db
from app.models import AdminAction, Server, ServerMetric, utcnow
from app.services.authorization import authorize_group, issue_code
from app.services.encryption import decrypt
from app.services.installation import (
    expire_installations,
    issue_installation_token,
    public_base_url,
    token_hash,
)
from app.services.installation_script import installation_script
from app.services.ssh_client import SSHFailure, connect, parse_private_key
from app.services.ssh_key_manager import fingerprint, generate_keypair, parse_host_key
from monitoring import ssh_dispatch
from monitoring.collectors import collect_server
from monitoring.worker import claim_server, monitor_one
from scripts import client_setup


@pytest.fixture
def managed(app):
    server = Server(
        name="Managed",
        ip_address="8.8.4.4",
        ssh_username="monitor",
        ssh_private_key_encrypted="",
        installation_status="PENDING_SETUP",
        is_active=False,
        services=["nginx.service"],
    )
    db.session.add(server)
    db.session.flush()
    generate_keypair(server)
    token = issue_installation_token(server, "test")
    db.session.commit()
    return server, token


@pytest.fixture
def host_key():
    return (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        .decode()
    )


def callback(client, server, token, action, **data):
    return client.post(
        f"/api/installations/{server.id}/{action}",
        json=data,
        headers={"Authorization": "Bearer " + token},
        base_url="https://localhost",
    )


def test_generated_keys_unique_encrypted_and_usable(managed):
    server, _ = managed
    first = server.ssh_public_key
    private = decrypt(server.ssh_private_key_encrypted)
    assert isinstance(parse_private_key(private), paramiko.Ed25519Key)
    assert private not in server.ssh_private_key_encrypted
    assert first.endswith(f"serverwatch-server-{server.id}")
    assert fingerprint(first) == server.ssh_key_fingerprint
    assert parse_private_key(private).get_base64() == first.split()[1]
    generate_keypair(server)
    assert first != server.ssh_public_key


def test_wizard_creates_without_connecting_or_manual_keys(client):
    with patch("app.routes.servers.connect") as ssh:
        response = client.post(
            "/servers/add",
            data={
                "name": "Wizard <test>",
                "ip_address": "8.8.4.4",
                "ssh_port": "2222",
                "monitoring_interval": "180",
                "installation_user": "ubuntu",
            },
        )
    ssh.assert_not_called()
    server = db.session.scalar(db.select(Server))
    assert server and server.installation_status == "PENDING_SETUP"
    assert server.ssh_username == "monitor" and server.installation_user == "ubuntu"
    assert server.installation_token_hash and not server.is_active
    assert not server.authorization_code_hash
    html = response.get_data(as_text=True)
    assert "installation-script" in html and "Wizard &lt;test&gt;" in html
    assert decrypt(server.ssh_private_key_encrypted) not in html
    assert server.ssh_private_key_encrypted not in html
    assert client.get(f"/servers/{server.id}/setup").status_code == 200
    assert 'id="installation-script"' not in client.get(f"/servers/{server.id}/setup").text
    assert 'name="private_key"' not in client.get(f"/servers/{server.id}/edit").text


@pytest.mark.parametrize(
    "field,value",
    [("ip_address", "localhost"), ("ssh_port", "0"), ("installation_user", "root; id")],
)
def test_wizard_rejects_bad_fields(client, field, value):
    fields = {
        "name": "Test",
        "ip_address": "8.8.4.4",
        "ssh_port": "22",
        "monitoring_interval": "180",
        "installation_user": "root",
    }
    fields[field] = value
    assert client.post("/servers/add", data=fields).status_code == 200
    assert db.session.scalar(db.select(Server.id)) is None


def test_script_syntax_payload_and_bash_escaping(managed):
    server, token = managed
    server.name = "test ' $(touch /tmp/NEVER) `id` <script>"
    script = installation_script(server, token)
    bashlex.parse(re.sub(r"<<'([A-Z_]+)'", r"<<\1", script))
    assert shlex.quote("Налаштування ServerWatch: " + server.name) in script
    body = script.split("<<'SERVERWATCH_CLIENT_SETUP'\n")[1].split("\nSERVERWATCH_CLIENT_SETUP")[0]
    ast.parse(body, feature_version=(3, 10))
    encoded = re.search(r"run\(json.loads\(base64.b64decode\('([^']+)'\)\)\)", body)[1]
    config = json.loads(base64.b64decode(encoded))
    assert config["token"] == token and config["server_id"] == server.id
    assert token_hash(token) == server.installation_token_hash
    assert token != server.installation_token_hash
    assert config["public_key"] == server.ssh_public_key
    assert not config["enable_swap"] and not config["swap_helper"]
    dispatcher = base64.b64decode(config["files"]["/usr/local/lib/serverwatch/ssh_dispatch.py"])
    assert b"serverwatch-collect" in dispatcher
    for secret in (decrypt(server.ssh_private_key_encrypted), server.ssh_private_key_encrypted):
        assert secret not in script and secret not in json.dumps(config)


def test_callback_and_worker_full_flow(app, client, managed, host_key, sample):
    server, token = managed
    assert not claim_server(server.id)
    assert callback(client, server, token, "started").json == {"status": "INSTALLING"}
    assert callback(client, server, token, "started").status_code == 200
    response = callback(client, server, token, "completed", host_key=host_key)
    assert response.status_code == 202 and response.json == {"status": "VERIFYING"}
    assert server.ssh_host_key == host_key and server.ssh_host_key_fingerprint == fingerprint(
        host_key
    )
    assert server.installation_token_used_at and not server.installation_completed_at
    assert not server.is_active and not server.authorization_code_hash
    assert callback(client, server, token, "completed", host_key=host_key).status_code == 409
    assert callback(client, server, token, "failed", error_code="internal").status_code == 409
    assert claim_server(server.id)
    with (
        patch("monitoring.collectors.connect", return_value=nullcontext(MagicMock())),
        patch("monitoring.collectors.execute", return_value=json.dumps(sample)) as execute,
    ):
        monitor_one(server.id)
    assert execute.call_args.args[1] == "serverwatch-collect"
    assert json.loads(execute.call_args.kwargs["input_data"])["services"] == ["nginx.service"]
    assert server.installation_status == "READY" and server.is_active
    assert server.installation_completed_at
    assert db.session.scalar(db.select(ServerMetric.id))
    assert not server.authorization_code_hash
    response = client.post(f"/servers/{server.id}/code")
    code = re.search(r'id="authorization-code">(\d{15})</code>', response.text)[1]
    assert code != server.authorization_code_hash
    assert (
        authorize_group(code, -100555, app.config["TELEGRAM_ADMIN_ID"]).installation_status
        == "ACTIVE"
    )
    assert server.telegram_authorized_at
    with pytest.raises(ValueError):
        authorize_group(code, -100555, app.config["TELEGRAM_ADMIN_ID"])
    actions = list(db.session.scalars(db.select(AdminAction.action)))
    assert {
        "installation_started",
        "installation_verifying",
        "installation_ready",
        "authorize_group",
    } <= set(actions)


def test_verification_failure_never_marks_ready(client, managed, host_key):
    server, token = managed
    callback(client, server, token, "completed", host_key=host_key)
    with (
        patch("monitoring.worker.collect_server", side_effect=SSHFailure("SSH-порт недоступний.")),
        patch("monitoring.worker.time.sleep"),
    ):
        monitor_one(server.id)
    assert server.installation_status == "CONNECTION_FAILED"
    assert not server.installation_completed_at and not server.is_active
    with pytest.raises(ValueError):
        issue_code(server)
    assert not claim_server(server.id)
    old_key = server.ssh_public_key
    assert client.post(f"/servers/{server.id}/setup/script").status_code == 200
    assert server.installation_status == "PENDING_SETUP" and old_key == server.ssh_public_key
    assert callback(client, server, token, "started").status_code == 403


def test_expired_token_and_cleanup(client, managed, host_key):
    server, token = managed
    server.installation_token_expires_at = utcnow() - timedelta(seconds=1)
    db.session.commit()
    assert callback(client, server, token, "completed", host_key=host_key).status_code == 410
    expire_installations()
    assert server.installation_status == "INSTALLATION_FAILED"


def test_token_bound_to_server_and_no_http(client, managed, server):
    managed_server, token = managed
    assert callback(client, server, token, "started").status_code == 403
    assert managed_server.installation_status == "PENDING_SETUP"
    assert (
        client.post(
            f"/api/installations/{managed_server.id}/started",
            json={},
            headers={"Authorization": "Bearer " + token},
        ).status_code
        == 400
    )


@pytest.mark.parametrize("value", [None, "bad", "ssh-ed25519 !!!", "ssh-ed25519 YQ==", {}, []])
def test_bad_host_keys_sanitized(client, managed, value):
    server, token = managed
    response = callback(client, server, token, "completed", host_key=value)
    assert response.status_code == 400
    assert not server.installation_token_used_at
    assert server.installation_status == "PENDING_SETUP"


def test_pinned_host_cannot_be_replaced(client, managed, host_key):
    server, token = managed
    server.ssh_host_key = server.ssh_public_key.rsplit(" ", 1)[0]
    db.session.commit()
    assert callback(client, server, token, "completed", host_key=host_key).status_code == 409
    assert server.ssh_host_key != host_key


def test_install_failure_accepts_only_static_error(client, managed):
    server, token = managed
    response = callback(client, server, token, "failed", error_code={"secret": token})
    assert response.status_code == 200
    assert server.installation_status == "INSTALLATION_FAILED"
    assert token not in server.installation_error
    assert token not in response.text


def test_callback_bruteforce_limited(client, managed):
    server, token = managed
    for _ in range(5):
        assert callback(client, server, "x" * 32, "started").status_code == 403
    assert callback(client, server, token, "started").status_code == 429


def test_admin_and_csrf_required(app, client, managed):
    server, _ = managed
    for path in (
        "/servers/add",
        f"/servers/{server.id}/setup/script",
        f"/servers/{server.id}/code",
    ):
        assert app.test_client().post(path).status_code == 302
    assert app.test_client().get(f"/api/servers/{server.id}/setup-status").status_code == 302
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post(f"/servers/{server.id}/setup/script").status_code == 400


def test_api_never_exports_tokens_or_keys(client, managed):
    server, token = managed
    for path in (f"/api/servers/{server.id}", f"/api/servers/{server.id}/setup-status"):
        response = client.get(path)
        assert response.status_code == 200
        for secret in (
            token,
            server.installation_token_hash,
            server.ssh_public_key,
            server.ssh_private_key_encrypted,
            decrypt(server.ssh_private_key_encrypted),
        ):
            assert secret not in response.text


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://example.com",
        "https://user:pass@example.com",
        "https://example.com/path",
        "https://example.com/?token=x",
        "https://example.com:0",
        "https://bad host",
    ],
)
def test_base_url_validated(app, url):
    app.config["PUBLIC_BASE_URL"] = url
    with pytest.raises(ValueError):
        public_base_url()


def test_pin_loaded_into_paramiko_without_known_hosts(managed, host_key):
    server, _ = managed
    server.ssh_port = 2222
    server.ssh_host_key = host_key
    ssh = MagicMock()
    with patch("app.services.ssh_client.paramiko.SSHClient", return_value=ssh):
        with connect(server):
            pass
    ssh.load_host_keys.assert_not_called()
    args = ssh.get_host_keys().add.call_args.args
    assert args[:2] == ("[8.8.4.4]:2222", "ssh-ed25519")
    assert args[2].get_base64() == host_key.split()[1]
    assert isinstance(ssh.set_missing_host_key_policy.call_args.args[0], paramiko.RejectPolicy)
    assert ssh.connect.call_args.kwargs["allow_agent"] is False
    ssh.close.assert_called_once()


def test_wrong_host_key_rejected(managed, host_key):
    server, _ = managed
    server.ssh_host_key = host_key
    ssh = MagicMock()
    key = parse_host_key(host_key)[0]
    ssh.connect.side_effect = paramiko.BadHostKeyException(server.ip_address, key, key)
    with patch("app.services.ssh_client.paramiko.SSHClient", return_value=ssh):
        with pytest.raises(SSHFailure, match="змінився"):
            with connect(server):
                pass


def test_collector_rejects_bogus_metrics(managed):
    server, _ = managed
    with (
        patch("monitoring.collectors.connect", return_value=nullcontext(MagicMock())),
        patch("monitoring.collectors.execute", return_value='{"cpu_usage": "secret"}'),
    ):
        with pytest.raises(SSHFailure, match="Некоректна відповідь"):
            collect_server(server)


def test_authorized_keys_idempotent_preserves_others(managed):
    server, _ = managed
    existing = "ssh-ed25519 unrelated comment"
    addition = client_setup.key_addition(existing, server.ssh_public_key)
    assert addition.startswith('\nrestrict,command="/usr/local/lib/serverwatch/ssh_dispatch.py" ')
    final = existing + addition
    assert final.startswith(existing)
    assert client_setup.key_addition(final, server.ssh_public_key) == ""
    with pytest.raises(client_setup.SetupError, match="інші обмеження"):
        client_setup.key_addition(server.ssh_public_key, server.ssh_public_key)


def test_installer_https_bearer_and_verified_tls():
    config = {"base_url": "https://8.8.4.4", "server_id": 1, "token": "SECRET-TOKEN"}
    with patch("scripts.client_setup.build_opener") as factory:
        http = factory.return_value.open
        client_setup.notify(config, "started")
    request = http.call_args.args[0]
    assert "SECRET" not in request.full_url
    assert request.get_header("Authorization") == "Bearer SECRET-TOKEN"
    context = factory.call_args.args[0]._context
    assert context.check_hostname and context.verify_mode != 0
    with patch("scripts.client_setup.build_opener") as factory:
        factory.return_value.open.side_effect = URLError("SECRET-TOKEN")
        with pytest.raises(client_setup.SetupError) as error:
            client_setup.notify(config, "started")
    assert "SECRET-TOKEN" not in str(error.value)


def test_consumed_token_stops_script_before_client_changes():
    with (
        patch.object(client_setup.os, "geteuid", return_value=0, create=True),
        patch(
            "scripts.client_setup.notify", side_effect=client_setup.SetupError("Токен використаний")
        ),
        patch("scripts.client_setup.ensure_account") as account,
        patch("scripts.client_setup.write_asset") as files,
    ):
        with pytest.raises(client_setup.SetupError):
            client_setup.install({})
    account.assert_not_called()
    files.assert_not_called()


def test_installer_mocked_repeat_and_optional_swap_only_installs_helper(host_key):
    config = {
        "files": {},
        "enable_swap": True,
        "swap_helper": base64.b64encode(b"helper").decode(),
        "public_key": "ssh-ed25519 dummy test",
    }
    # Mock every OS/network side effect; no actual client files or packages are touched.
    with (
        patch.object(client_setup.os, "geteuid", return_value=0, create=True),
        patch("scripts.client_setup.Path") as path,
        patch("scripts.client_setup.shutil.which", return_value="present"),
        patch("scripts.client_setup.notify") as notify,
        patch("scripts.client_setup.ensure_account", return_value="account"),
        patch("scripts.client_setup.add_authorized_key") as keys,
        patch("scripts.client_setup.write_asset") as files,
        patch("scripts.client_setup.host_public_key", return_value=host_key),
        patch("scripts.client_setup.run_command") as command,
        patch("scripts.client_setup.os.unlink"),
    ):
        path.return_value.read_text.return_value = "ID=ubuntu\n"
        path.return_value.is_dir.return_value = True
        client_setup.install(config)
        client_setup.install(config)
    assert [call.args[1] for call in notify.call_args_list] == [
        "started",
        "completed",
        "started",
        "completed",
    ]
    assert keys.call_count == 2
    assert command.call_count == 2
    assert all(call.args[0][0] == "/usr/sbin/visudo" for call in command.call_args_list)
    rules = [
        call.args[1] for call in files.call_args_list if call.args[0].startswith("/etc/sudoers.d/")
    ]
    assert len(rules) == 2 and b'NOPASSWD: /usr/local/sbin/serverwatch-swap ""' in rules[0]
    assert b"ALL=(ALL)" not in rules[0]


def test_installer_failure_never_reports_completed():
    with (
        patch.object(client_setup.os, "geteuid", return_value=0, create=True),
        patch("scripts.client_setup.Path") as path,
        patch("scripts.client_setup.notify") as notify,
    ):
        path.return_value.read_text.return_value = "ID=unsupported\n"
        with pytest.raises(client_setup.SetupError, match="Ubuntu"):
            client_setup.install({})
    assert [call.args[1] for call in notify.call_args_list] == ["started", "failed"]
    assert notify.call_args.kwargs == {"error_code": "unsupported_os"}


def test_installer_refuses_callback_redirect():
    with pytest.raises(client_setup.SetupError, match="Перенаправлення"):
        client_setup.NoRedirect().redirect_request(None, None, 302, "", {}, "http://other/secret")


def test_worker_discards_metrics_if_script_reissued_during_collection(managed, sample):
    server, _ = managed
    server.installation_status = "CONNECTION_FAILED"
    server.is_active = True
    db.session.commit()

    def collect(_):
        issue_installation_token(server, "admin")
        db.session.commit()
        return sample

    with patch("monitoring.worker.collect_server", side_effect=collect):
        monitor_one(server.id)
    assert server.installation_status == "PENDING_SETUP" and not server.is_active
    assert not db.session.scalar(db.select(ServerMetric.id))


@pytest.mark.parametrize(
    "operation", ["", "id", "bash", "serverwatch-collect; id", "serverwatch-swap x"]
)
def test_forced_dispatcher_denies_arbitrary_commands(monkeypatch, operation):
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", operation)
    with patch("monitoring.ssh_dispatch.subprocess.run") as run:
        assert ssh_dispatch.main() == 126
    run.assert_not_called()


def test_forced_collection_matches_protocol(monkeypatch, capsys):
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "serverwatch-collect")
    stdin = SimpleNamespace(
        buffer=io.BytesIO(json.dumps({"services": ["nginx.service"], "since": 1}).encode())
    )
    collector = MagicMock(return_value={"cpu_usage": 1})
    with (
        patch.object(sys, "stdin", stdin),
        patch.dict(sys.modules, {"remote_collect": SimpleNamespace(collect=collector)}),
    ):
        assert ssh_dispatch.main() == 0
    collector.assert_called_once_with(["nginx.service"], 1)
    assert json.loads(capsys.readouterr().out)["cpu_usage"] == 1


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"services": ["$(id).service"], "since": 0},
        {"services": [], "since": True},
        {"since": -1},
    ],
)
def test_forced_collection_rejects_untrusted_input(monkeypatch, data):
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "serverwatch-collect")
    with patch.object(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(data).encode()))):
        with pytest.raises(ValueError):
            ssh_dispatch.main()
