#!/usr/bin/python3 -I
"""Forced command for the monitor key. Only fixed operations are accepted."""

import json
import os
import re
import subprocess
import sys
import time


def main():
    operation = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    if operation == "serverwatch-collect":
        payload = sys.stdin.buffer.read(8193)
        if len(payload) > 8192:
            raise ValueError
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError
        services = data.get("services", [])
        since = data.get("since")
        if (
            not isinstance(services, list)
            or len(services) > 20
            or any(
                not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9_.@-]{1,90}\.service", name)
                for name in services
            )
            or type(since) is not int
            or not 0 <= since <= time.time() + 300
        ):
            raise ValueError
        sys.path.insert(0, "/usr/local/lib/serverwatch")
        from remote_collect import collect

        print(json.dumps(collect(services, since)))
        return 0
    if operation == "serverwatch-swap":
        result = subprocess.run(
            ["/usr/bin/sudo", "-n", "/usr/local/sbin/serverwatch-swap"],
            capture_output=True,
            timeout=130,
            check=False,
        )
        if result.returncode:
            print(json.dumps({"status": "denied", "reason": "Операцію SWAP не дозволено."}))
        else:
            sys.stdout.write(result.stdout[:4096].decode("utf-8", errors="replace"))
        return 0
    sys.stderr.write("Доступні тільки фіксовані операції ServerWatch.\n")
    return 126


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError):
        sys.stderr.write("Операцію ServerWatch відхилено.\n")
        raise SystemExit(1) from None
