import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import bashlex
import pytest
from cryptography.fernet import Fernet
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

from scripts import manage, restore_database_users, setup_environment

ROOT = Path(__file__).parents[1]


def test_generated_environment_matches_actual_configuration(tmp_path):
    files = setup_environment.build_files("tunnel", "12345:dummy_token", 12345)
    path = tmp_path / "serverwatch.env"
    path.write_text(files["serverwatch.env"], encoding="utf-8")
    values = dotenv_values(path)
    example_keys = set(dotenv_values(ROOT / ".env.example"))
    assert set(values) == example_keys
    assert values["SESSION_COOKIE_SECURE"] == "false"
    assert len(values["SECRET_KEY"]) == 64
    Fernet(values["ENCRYPTION_KEY"].encode())
    runtime = make_url(values["DATABASE_URL"])
    assert runtime.username == "serverwatch" and runtime.host == "127.0.0.1"
    assert runtime.database == "serverwatch" and len(runtime.password) == 64
    assert runtime.password in files["bootstrap.sql"]
    assert "utf8mb4_unicode_ci" in files["bootstrap.sql"]
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" in files["bootstrap.sql"]
    assert "GRANT ALL PRIVILEGES ON serverwatch.*" in files["bootstrap.sql"]
    assert runtime.password not in files["migration.env"]


def test_https_generation_and_unique_secrets():
    first = setup_environment.build_files("https", "123:dummy", 1)
    second = setup_environment.build_files("https", "123:dummy", 1)
    assert "SESSION_COOKIE_SECURE=true" in first["serverwatch.env"]
    assert first["serverwatch.env"] != second["serverwatch.env"]
    url = setup_environment.database_url("serverwatch", "special:@/&#% password")
    assert make_url(url).password == "special:@/&#% password"


def test_management_reads_migration_credential_only_for_upgrade(tmp_path, monkeypatch):
    files = setup_environment.build_files("tunnel", "123:dummy", 1)
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    monkeypatch.setattr(manage, "DIRECTORY", tmp_path)
    runtime = manage.read_environment()
    migration = manage.read_environment(migration=True)
    assert make_url(runtime["DATABASE_URL"]).username == "serverwatch"
    assert make_url(migration["DATABASE_URL"]).username == "serverwatch_migrate"
    assert runtime["ENCRYPTION_KEY"] == migration["ENCRYPTION_KEY"]


def test_restoring_users_refuses_existing_roles():
    connection = MagicMock()
    cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = [2]
    with (
        patch.object(restore_database_users.os, "geteuid", return_value=0, create=True),
        patch.object(
            restore_database_users, "credentials", return_value=[("serverwatch", "secret")]
        ),
        patch.object(restore_database_users.pymysql, "connect", return_value=connection),
    ):
        with pytest.raises(ValueError):
            restore_database_users.main()
    assert cursor.execute.call_count == 1


def test_restore_uses_original_password_as_bound_parameter():
    connection = MagicMock()
    cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = [0]
    with (
        patch.object(restore_database_users.os, "geteuid", return_value=0, create=True),
        patch.object(
            restore_database_users,
            "credentials",
            return_value=[("serverwatch", "special'password")],
        ),
        patch.object(restore_database_users.pymysql, "connect", return_value=connection),
    ):
        restore_database_users.main()
    create_call = cursor.execute.call_args_list[1]
    assert "special'password" not in create_call.args[0]
    assert create_call.args[1] == ("special'password",)


def test_readme_units_and_repository_paths_are_real():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    units = re.findall(r"<!-- unit:([^ ]+) -->\s*```ini\n(.*?)\n```", readme, re.S)
    assert len(units) == 3
    for filename, content in units:
        assert content == (ROOT / filename).read_text(encoding="utf-8").rstrip()
    for filename in set(re.findall(r"(?:scripts|deploy)/[A-Za-z0-9_.-]+", readme)):
        assert (ROOT / filename).is_file(), filename
    for filename in re.findall(r"\[[^]]+\]\(([^):]+\.md)\)", readme):
        assert (ROOT / filename).is_file(), filename


def test_tunnel_config_and_proxy_cannot_bind_public_gunicorn():
    tunnel = (ROOT / "deploy/nginx-tunnel.conf").read_text()
    assert "listen 127.0.0.1:8080;" in tunnel
    assert "listen 8080;" not in tunnel
    proxy = (ROOT / "deploy/nginx-proxy.conf").read_text()
    assert "unix:/run/serverwatch/web.sock" in proxy
    assert "X-Forwarded-For $remote_addr" in proxy
    limits = (ROOT / "deploy/nginx-limits.conf").read_text()
    assert "POST:/(login|totp)" in limits
    monitor = (ROOT / "deploy/serverwatch-monitor.service").read_text()
    assert "flock --no-fork --nonblock" in monitor


def test_backup_fails_closed_and_only_publishes_after_validation():
    script = (ROOT / "scripts/serverwatch-backup").read_text()
    assert "set -euo pipefail" in script and "umask 077" in script
    assert "flock --nonblock" in script
    assert script.index("gzip --test") < script.index("/usr/bin/mv")
    assert "--single-transaction" in script and "--user=root" in script


def test_all_readme_bash_blocks_parse():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme += (ROOT / "docs/ip-https.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```bash\n(.*?)\n```", readme, re.S)
    assert len(blocks) > 30
    for number, content in enumerate(blocks, 1):
        try:
            # bashlex 0.18 does not remove quotes from heredoc delimiters.
            parsed_input = re.sub(r"<<'([A-Z_]+)'", r"<<\1", content)
            bashlex.parse(parsed_input + "\n")
        except bashlex.errors.ParsingError as error:
            pytest.fail(f"Bash block {number}: {error}")
    bashlex.parse((ROOT / "scripts/serverwatch-backup").read_text())


def test_upgrade_drops_root_before_running_flask(monkeypatch):
    order = []
    monkeypatch.setattr(manage.sys, "argv", ["manage.py", "upgrade"])
    monkeypatch.setattr(
        manage,
        "read_environment",
        lambda migration=False: {"DATABASE_URL": "ddl" if migration else "runtime"},
    )
    monkeypatch.setenv("DATABASE_URL", "initial")
    monkeypatch.setattr(manage, "drop_root", lambda: order.append("drop"))
    monkeypatch.setattr(manage.os, "chdir", lambda _: None)

    def run(command):
        order.append("flask")
        assert command[-4:] == ["--app", "run", "db", "upgrade"]
        assert manage.os.environ["DATABASE_URL"] == "ddl"
        return 0

    monkeypatch.setattr(manage.subprocess, "call", run)
    assert manage.main() == 0
    assert order == ["drop", "flask"]


def test_ip_bootstrap_never_exposes_login_over_plain_http():
    config = (ROOT / "deploy/nginx-ip-bootstrap.conf").read_text()
    assert "location ^~ /.well-known/acme-challenge/" in config
    assert "return 404;" in config
    assert "proxy_pass" not in config and "serverwatch-proxy.conf" not in config


def test_ip_tls_paths_match_certificate_name_and_renewal_hook():
    config = (ROOT / "deploy/nginx-ip-https.conf").read_text()
    assert "listen 443 ssl;" in config
    assert "/etc/letsencrypt/live/serverwatch-ip/fullchain.pem" in config
    assert "/etc/letsencrypt/live/serverwatch-ip/privkey.pem" in config
    assert "return 301 https://$server_name$request_uri;" in config
    assert "serverwatch-proxy.conf" in config
    unit = (ROOT / "deploy/serverwatch-certbot.service").read_text()
    assert "--cert-name serverwatch-ip" in unit
    assert "--deploy-hook" in unit and "nginx -t" in unit and "reload nginx" in unit
    assert "/opt/serverwatch-certbot/bin/certbot renew" in unit
    timer = (ROOT / "deploy/serverwatch-certbot.timer").read_text()
    assert "00,06,12,18:15:00 UTC" in timer and "Persistent=true" in timer


def test_gunicorn_control_socket_is_disabled_without_weakening_filesystem():
    unit = (ROOT / "deploy/serverwatch-web.service").read_text()
    assert "--no-control-socket" in unit
    assert "ProtectSystem=strict" in unit
    assert "ReadWritePaths=/opt/serverwatch/instance" in unit
    assert "ReadWritePaths=/opt/serverwatch\n" not in unit
    requirements = (ROOT / "requirements.txt").read_text()
    assert "gunicorn>=25.3,<26" in requirements
