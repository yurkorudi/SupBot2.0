import base64
import json
import shlex
from pathlib import Path

from flask import current_app

from app.extensions import db
from app.models import ServerBackup, ServerCpuRecovery, ServerTerminalAccess

from .installation import public_base_url


def installation_script(server, token):
    root = Path(current_app.root_path).parent
    files = {
        "/usr/local/lib/serverwatch/ssh_dispatch.py": root / "monitoring/ssh_dispatch.py",
        "/usr/local/lib/serverwatch/remote_collect.py": root / "monitoring/remote_collect.py",
        "/usr/local/lib/serverwatch/terminal-shell": root / "scripts/terminal-shell",
        "/usr/local/lib/serverwatch/terminal.bashrc": root / "scripts/terminal.bashrc",
        "/usr/local/sbin/serverwatch-docker-list": root / "scripts/serverwatch-docker-list",
        "/usr/local/sbin/serverwatch-db-export": root / "scripts/serverwatch-db-export",
    }
    terminal = db.session.get(ServerTerminalAccess, server.id)
    backup = db.session.get(ServerBackup, server.id)
    recovery = db.session.get(ServerCpuRecovery, server.id)
    config = {
        "base_url": public_base_url(),
        "server_id": server.id,
        "token": token,
        "public_key": server.ssh_public_key,
        "ssh_port": server.ssh_port,
        "server_name": server.name,
        "installation_user": server.installation_user,
        "enable_swap": server.swap_enabled,
        "terminal_public_key": terminal.ssh_public_key if terminal else "",
        "enable_docker_inventory": True,
        "enable_database_backups": bool(backup and backup.enabled),
        "enable_reboot": bool(recovery and recovery.enabled),
        "files": {
            filename: base64.b64encode(path.read_text(encoding="utf-8").encode()).decode()
            for filename, path in files.items()
        },
        "swap_helper": base64.b64encode((root / "scripts/serverwatch-swap").read_bytes()).decode()
        if server.swap_enabled
        else "",
        "persistent_swap_helper": (
            base64.b64encode((root / "scripts/serverwatch-swap-persistent").read_bytes()).decode()
            if server.swap_enabled
            else ""
        ),
    }
    encoded = base64.b64encode(json.dumps(config, ensure_ascii=True).encode()).decode()
    source = (root / "scripts/client_setup.py").read_text(encoding="utf-8")
    return f"""#!/usr/bin/env bash
# ServerWatch: server_id={server.id}; токен діє одну годину.
# Виконайте у Bash на клієнтському сервері. Приватного SSH-ключа тут немає.
# Публічний ключ моніторингу: {server.ssh_public_key}
set +o history 2>/dev/null || true
(
set -eu
printf '%s\\n' {shlex.quote("Налаштування ServerWatch: " + server.name)}
serverwatch_root() {{
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        sudo "$@"
    fi
}}
if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null || {{ printf '%s\\n' 'Потрібен root або sudo.'; exit 1; }}
    sudo -v
fi
if ! command -v python3 >/dev/null; then
    command -v apt-get >/dev/null || {{ printf '%s\\n' 'Потрібні Ubuntu/Debian і Python 3.'; exit 1; }}
    serverwatch_root apt-get update
    serverwatch_root apt-get install -y python3 ca-certificates
fi
serverwatch_root python3 - <<'SERVERWATCH_CLIENT_SETUP'
{source}
run(json.loads(base64.b64decode({encoded!r})))
SERVERWATCH_CLIENT_SETUP
)
"""
