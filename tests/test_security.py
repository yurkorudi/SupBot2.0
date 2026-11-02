from datetime import timedelta
from unittest.mock import MagicMock, patch

import paramiko
import pyotp
import pytest

from app.extensions import db
from app.models import User, utcnow
from app.services.authorization import authorize_group, issue_code
from app.services.encryption import decrypt, encrypt
from app.services.security import require_admin, verify_totp
from app.services.ssh_client import SSHFailure, connect


def test_encryption(app):
    secret = "private-key"
    token = encrypt(secret)
    assert secret not in token
    assert decrypt(token) == secret
    assert encrypt(secret) != token


def test_code_single_use(server):
    code = issue_code(server)
    db.session.commit()
    assert len(code) == 15 and code.isascii() and code.isdigit()
    assert server.authorization_code_hash != code
    assert authorize_group(code, -100123, 123456).id == server.id
    assert server.authorization_code_hash is None
    with pytest.raises(ValueError):
        authorize_group(code, -100123, 123456)


def test_expired_code(server):
    code = issue_code(server)
    server.authorization_expires_at = utcnow() - timedelta(seconds=1)
    db.session.commit()
    with pytest.raises(ValueError):
        authorize_group(code, -100123, 123456)


def test_wrong_group(server):
    code = issue_code(server)
    db.session.commit()
    with pytest.raises(ValueError):
        authorize_group(code, -200, 123456)


def test_binding_requires_numeric_admin(server):
    code = issue_code(server)
    db.session.commit()
    for actor in (999, "Yureweo", "123456"):
        with pytest.raises(PermissionError):
            authorize_group(code, -100123, actor)
    require_admin(123456)


def test_totp_replay_and_invalid(user):
    assert not verify_totp(user, "bad")
    code = pyotp.TOTP("JBSWY3DPEHPK3PXP").at(1800000000)
    assert verify_totp(user, code, at=1800000000)
    db.session.commit()
    assert not verify_totp(user, code, at=1800000000)


def test_key_not_exposed(client, server):
    response = client.get(f"/api/servers/{server.id}")
    assert response.status_code == 200
    for secret in ("PRIVATE-KEY-NEVER-RETURN", server.ssh_private_key_encrypted, "ssh_private_key"):
        assert secret not in response.get_data(as_text=True)
        assert secret not in client.get(f"/servers/{server.id}/edit").get_data(as_text=True)


def test_api_requires_login(app, server):
    assert app.test_client().get(f"/api/servers/{server.id}").status_code == 302


def test_login_requires_totp(app, user):
    client = app.test_client()
    response = client.post(
        "/login", data={"username": "admin", "password": "a-unique-long-test-password"}
    )
    assert response.location.endswith("/totp")
    assert client.get("/").status_code == 302
    code = pyotp.TOTP("JBSWY3DPEHPK3PXP").now()
    assert client.post("/totp", data={"code": code}).status_code == 302
    assert client.get("/").status_code == 200
    assert client.get("/totp/qr").status_code == 403


def test_first_setup_qr_is_protected(app, user):
    user.totp_enabled = False
    db.session.commit()
    client = app.test_client()
    assert client.get("/totp/qr").status_code == 403
    client.post("/login", data={"username": "admin", "password": "a-unique-long-test-password"})
    assert client.get("/totp/qr").status_code == 200
    assert client.get("/").status_code == 302


def test_login_bruteforce(app, user):
    client = app.test_client()
    for _ in range(5):
        client.post("/login", data={"username": "admin", "password": "wrong"})
    assert client.post("/login", data={"username": "admin", "password": "wrong"}).status_code == 429


def test_csrf_enforced(app, user):
    app.config["WTF_CSRF_ENABLED"] = True
    assert app.test_client().post("/login", data={}).status_code == 400


def test_ssh_timeout_closes_client(app, server, tmp_path):
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("")
    app.config["SSH_KNOWN_HOSTS"] = str(known_hosts)
    client = MagicMock()
    client.connect.side_effect = TimeoutError()
    with (
        patch("app.services.ssh_client.paramiko.SSHClient", return_value=client),
        patch("app.services.ssh_client.parse_private_key", return_value=MagicMock()),
    ):
        with pytest.raises(SSHFailure, match="очікування"):
            with connect(server):
                pass
    client.close.assert_called_once()
    assert isinstance(client.set_missing_host_key_policy.call_args.args[0], paramiko.RejectPolicy)


def test_create_admin_cli(app):
    result = app.test_cli_runner().invoke(
        args=["create-admin", "--username", "new", "--password", "long-unique-password-123"]
    )
    assert result.exit_code == 0
    user = db.session.scalar(db.select(User).where(User.username == "new"))
    assert user and not user.totp_enabled
