import json
import math
import shlex
from datetime import UTC, timedelta
from pathlib import Path

from app.models import utcnow
from app.services.ssh_client import SSHFailure, connect, execute

SOURCE = Path(__file__).with_name("remote_collect.py").read_text(encoding="utf-8")


def collect_server(server):
    since = server.last_success_at or (utcnow() - timedelta(hours=4))
    stamp = int(since.replace(tzinfo=UTC).timestamp())
    command = (
        "python3 - "
        + shlex.quote(json.dumps(server.services))
        + f" {stamp} <<'SERVERWATCH_COLLECT'\n"
    )
    command += SOURCE + "\nSERVERWATCH_COLLECT"
    with connect(server) as client:
        try:
            data = json.loads(execute(client, command, timeout=90))
            for field in ("cpu_usage", "ram_percent", "swap_percent", "disk_percent"):
                if not isinstance(data[field], (int, float)) or not math.isfinite(data[field]):
                    raise ValueError
                if not 0 <= data[field] <= 100:
                    raise ValueError
            for field in (
                "ram_total",
                "ram_used",
                "swap_total",
                "swap_used",
                "disk_total",
                "disk_used",
            ):
                if not isinstance(data[field], int) or data[field] < 0:
                    raise ValueError
            if not isinstance(data["details"], dict):
                raise ValueError
            return data
        except (ValueError, KeyError, TypeError):
            raise SSHFailure("Некоректна відповідь збирача метрик.") from None
