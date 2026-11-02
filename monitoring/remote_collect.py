"""Executed on Linux via python3 stdin; requires only the standard library."""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


def read(path):
    return Path(path).read_text()


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=3, check=False)
        return result.stdout[:32768]
    except (OSError, subprocess.TimeoutExpired):
        return ""


def cpu_ticks():
    values = list(map(int, read("/proc/stat").splitlines()[0].split()[1:9]))
    return sum(values), values[3] + values[4]


def parse_service_event(line, services):
    try:
        event = json.loads(line)
        if not isinstance(event, dict):
            return None
        unit = next(
            (
                value
                for value in (event.get("UNIT"), event.get("_SYSTEMD_UNIT"))
                if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.@-]{1,90}\.service", value)
            ),
            None,
        )
        if not unit:
            return None
        priority = int(event.get("PRIORITY", 6))
        message = str(event.get("MESSAGE", ""))
        failed = bool(re.search(r"\bfailed\b", message, re.IGNORECASE))
        if priority <= 3 or failed:
            level, classification = "error", "Помилка системного сервісу"
        elif priority == 4:
            level, classification = "warning", "Попередження системного сервісу"
        elif unit not in services:
            return None
        elif re.search(r"\brestart", message, re.IGNORECASE):
            level, classification = "notice", "Перезапуск сервісу"
        elif re.search(r"\b(?:Started|Stopped)\b", message):
            level, classification = "notice", "Зміна стану сервісу"
        else:
            return None
        return {
            "service": unit,
            "level": level,
            "message": classification,
            "timestamp": int(event["__REALTIME_TIMESTAMP"]) // 1000000,
        }
    except (ValueError, KeyError, TypeError):
        return None


def collect(services, since):
    before, idle_before = cpu_ticks()
    time.sleep(0.25)
    after, idle_after = cpu_ticks()
    cpu = 100 * (1 - (idle_after - idle_before) / max(1, after - before))
    mem = {}
    for line in read("/proc/meminfo").splitlines():
        key, value = line.split(":", 1)
        mem[key] = int(value.strip().split()[0]) * 1024
    total, available = mem["MemTotal"], mem.get("MemAvailable", mem["MemFree"])
    swap_total = mem.get("SwapTotal", 0)
    swap_used = swap_total - mem.get("SwapFree", 0)
    root = shutil.disk_usage("/")
    filesystems = []
    seen = set()
    for line in read("/proc/mounts").splitlines():
        device, mount, _kind = line.split()[:3]
        if not device.startswith("/dev/") or mount in seen:
            continue
        seen.add(mount)
        try:
            disk = shutil.disk_usage(mount)
            filesystems.append(
                {
                    "mount": mount,
                    "total": disk.total,
                    "used": disk.used,
                    "free": disk.free,
                    "percent": round(disk.used / disk.total * 100, 2),
                }
            )
        except OSError:
            continue
    interfaces = []
    for line in read("/proc/net/dev").splitlines()[2:]:
        name, values = line.split(":", 1)
        name = name.strip()
        values = values.split()
        try:
            state = read(f"/sys/class/net/{name}/operstate").strip()
        except OSError:
            state = "unknown"
        interfaces.append(
            {"name": name, "state": state, "in": int(values[0]), "out": int(values[8])}
        )
    states = {}
    for service in services[:20]:
        if re.fullmatch(r"[A-Za-z0-9_.@-]{1,90}\.service", service):
            states[service] = (
                command(["systemctl", "is-active", "--", service]).strip() or "unknown"
            )
    # Export only unit names and enumerated events, never raw journal MESSAGE content.
    events = []
    lines = command(
        ["journalctl", "--since", f"@{since}", "-p", "0..4", "-n", "30", "-o", "json", "--no-pager"]
    ).splitlines()
    for line in lines:
        event = parse_service_event(line, services)
        if event:
            events.append(event)
    for name, state in states.items():
        if state == "failed":
            events.append(
                {
                    "service": name,
                    "level": "error",
                    "message": "Сервіс у стані failed",
                    "timestamp": int(time.time()),
                }
            )
    lifecycle = command(
        ["journalctl", "--since", f"@{since}", "_PID=1", "-n", "60", "-o", "json", "--no-pager"]
    ).splitlines()
    for line in lifecycle:
        event = parse_service_event(line, services)
        if event:
            events.append(event)
    load = os.getloadavg()
    return {
        "cpu_usage": round(max(0, min(100, cpu)), 2),
        "ram_total": total,
        "ram_used": total - available,
        "ram_percent": round((total - available) / total * 100, 2),
        "swap_total": swap_total,
        "swap_used": swap_used,
        "swap_percent": round(swap_used / swap_total * 100, 2) if swap_total else 0,
        "disk_total": root.total,
        "disk_used": root.used,
        "disk_percent": round(root.used / root.total * 100, 2),
        "details": {
            "ram_free": mem["MemFree"],
            "ram_available": available,
            "swap_free": swap_total - swap_used,
            "disk_free": root.free,
            "cpu_cores": os.cpu_count(),
            "load_average": list(load),
            "uptime": float(read("/proc/uptime").split()[0]),
            "processes": sum(1 for path in Path("/proc").iterdir() if path.name.isdigit()),
            "filesystems": filesystems[:30],
            "interfaces": interfaces[:30],
            "network_in": sum(i["in"] for i in interfaces if i["name"] != "lo"),
            "network_out": sum(i["out"] for i in interfaces if i["name"] != "lo"),
            "services": states,
            "errors": events[:30],
        },
    }


if __name__ == "__main__":
    print(json.dumps(collect(json.loads(sys.argv[1]), int(sys.argv[2]))))
