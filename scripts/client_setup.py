"""Embedded client installer. Receives public assets, never a private SSH key."""

import base64
import json
import os
import shutil
import ssl
import stat
import subprocess
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


class SetupError(Exception):
    pass


def run_command(args):
    result = subprocess.run(args, capture_output=True, timeout=180, check=False)
    if result.returncode:
        raise SetupError("Системна команда завершилася помилкою; перевірте стан пакетів і права.")
    return result.stdout.decode("utf-8", errors="replace")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SetupError("Перенаправлення callback заборонено. Перевірте PUBLIC_BASE_URL.")


def notify(config, action, **values):
    request = Request(
        f"{config['base_url']}/api/installations/{config['server_id']}/{action}",
        data=json.dumps(values).encode(),
        method="POST",
        headers={"Authorization": "Bearer " + config["token"], "Content-Type": "application/json"},
    )
    try:
        opener = build_opener(HTTPSHandler(context=ssl.create_default_context()), NoRedirect())
        with opener.open(request, timeout=25) as response:
            response.read(4096)
    except HTTPError as error:
        raise SetupError(
            f"Backend відхилив підтвердження (HTTP {error.code}). Перевірте майстер і строк токена."
        ) from None
    except (URLError, OSError):
        raise SetupError("Backend недоступний або HTTPS-сертифікат не пройшов перевірку.") from None


def write_asset(path, content, mode):
    path = Path(path)
    if path.is_symlink():
        raise SetupError("Небезпечне символьне посилання замість файлу ServerWatch.")
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    parent = path.parent.stat()
    if parent.st_uid != 0 or parent.st_mode & 0o022:
        raise SetupError("Каталог ServerWatch має небезпечного власника або права.")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as target:
        temporary = target.name
        os.fchmod(target.fileno(), mode)
        target.write(content)
        target.flush()
        os.fsync(target.fileno())
    try:
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def ensure_account():
    import grp
    import pwd

    try:
        account = pwd.getpwnam("monitor")
    except KeyError:
        run_command(
            [
                "/usr/sbin/useradd",
                "--system",
                "--user-group",
                "--create-home",
                "--home-dir",
                "/var/lib/serverwatch-monitor",
                "--shell",
                "/bin/sh",
                "monitor",
            ]
        )
        account = pwd.getpwnam("monitor")
    groups = {
        group.gr_name
        for group in grp.getgrall()
        if "monitor" in group.gr_mem or group.gr_gid == account.pw_gid
    }
    if (
        account.pw_uid == 0
        or account.pw_gid == 0
        or groups & {"sudo", "admin", "wheel", "root"}
        or not account.pw_dir.startswith("/")
        or account.pw_dir == "/"
    ):
        raise SetupError("Наявний monitor має непридатний UID або домашній каталог.")
    if Path(account.pw_shell).name in ("nologin", "false"):
        raise SetupError(
            "Наявний monitor має nologin/false. Його сторонні налаштування не змінено."
        )
    try:
        grp.getgrnam("systemd-journal")
    except KeyError:
        pass
    else:
        run_command(["/usr/sbin/usermod", "-a", "-G", "systemd-journal", "monitor"])
    return account


def key_addition(
    existing, public_key, options='restrict,command="/usr/local/lib/serverwatch/ssh_dispatch.py"'
):
    line = options + " " + public_key
    blob = public_key.split()[1]
    matches = [entry.strip() for entry in existing.splitlines() if blob in entry.split()]
    if matches and matches != [line]:
        raise SetupError("Цей ключ уже має інші обмеження. Наявні рядки не змінено.")
    return (
        "" if matches else ("\n" if existing and not existing.endswith("\n") else "") + line + "\n"
    )


def add_authorized_key(account, public_key, *, terminal=False):
    home = os.open(account.pw_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            os.mkdir(".ssh", 0o700, dir_fd=home)
        except FileExistsError:
            pass
        ssh = os.open(".ssh", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=home)
        try:
            os.fchown(ssh, account.pw_uid, account.pw_gid)
            os.fchmod(ssh, 0o700)
            descriptor = os.open(
                "authorized_keys",
                os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                0o600,
                dir_fd=ssh,
            )
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid not in (0, account.pw_uid)
            ):
                os.close(descriptor)
                raise SetupError("authorized_keys має небезпечний тип або власника.")
            os.fchown(descriptor, account.pw_uid, account.pw_gid)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "r+", encoding="utf-8") as target:
                existing = target.read(1024 * 1024 + 1)
                if len(existing) > 1024 * 1024:
                    raise SetupError("authorized_keys перевищує дозволений розмір.")
                addition = (
                    key_addition(
                        existing,
                        public_key,
                        'restrict,pty,command="/usr/local/lib/serverwatch/terminal-shell"',
                    )
                    if terminal
                    else key_addition(existing, public_key)
                )
                if addition:
                    target.write(addition)
                    target.flush()
                    os.fsync(target.fileno())
        finally:
            os.close(ssh)
    finally:
        os.close(home)
    run_command(["/usr/bin/ssh-keygen", "-lf", str(Path(account.pw_dir) / ".ssh/authorized_keys")])


def host_public_key():
    run_command(["/usr/sbin/sshd", "-t"])
    effective = run_command(["/usr/sbin/sshd", "-T"])
    if "pubkeyauthentication yes" not in effective:
        raise SetupError(
            "SSH не дозволяє ключову автентифікацію. Глобальну конфігурацію не змінено."
        )
    hosts = [
        line.split(maxsplit=1)[1] + ".pub"
        for line in effective.splitlines()
        if line.startswith("hostkey ")
    ]
    hosts.sort(key=lambda value: "ed25519" not in value)
    for filename in hosts:
        path = Path(filename)
        if path.is_file():
            parts = path.read_text().strip().split()
            if len(parts) >= 2 and parts[0] in (
                "ssh-ed25519",
                "ssh-rsa",
                "ecdsa-sha2-nistp256",
                "ecdsa-sha2-nistp384",
                "ecdsa-sha2-nistp521",
            ):
                return " ".join(parts[:2])
    raise SetupError("Не знайдено публічного ключа активного SSH-хоста.")


def ensure_terminal_account():
    import grp
    import pwd

    username = "serverwatch-terminal"
    home = "/var/lib/serverwatch-terminal"
    try:
        account = pwd.getpwnam(username)
    except KeyError:
        run_command(
            [
                "/usr/sbin/useradd",
                "--system",
                "--user-group",
                "--create-home",
                "--home-dir",
                home,
                "--shell",
                "/bin/bash",
                username,
            ]
        )
        account = pwd.getpwnam(username)
    if (
        account.pw_uid == 0
        or account.pw_gid == 0
        or account.pw_dir != home
        or account.pw_shell != "/bin/bash"
        or any(
            username in group.gr_mem and group.gr_gid != account.pw_gid for group in grp.getgrall()
        )
        or grp.getgrgid(account.pw_gid).gr_name != username
    ):
        raise SetupError("Обліковий запис термінала має непридатні права або налаштування.")
    return account


def write_sudo_rules(name, rule):
    temporary = f"/usr/local/lib/serverwatch/{name}.sudoers.check"
    write_asset(temporary, rule.encode(), 0o600)
    try:
        run_command(["/usr/sbin/visudo", "-cf", temporary])
        write_asset(f"/etc/sudoers.d/{name}", rule.encode(), 0o440)
    finally:
        os.unlink(temporary)


def install(config):
    if os.geteuid() != 0:
        raise SetupError("Для встановлення потрібен root або sudo.")
    started = False
    phase = "internal"
    try:
        notify(config, "started")
        started = True
        phase = "unsupported_os"
        release = Path("/etc/os-release").read_text()
        if not any(
            line in release.splitlines()
            for line in ("ID=ubuntu", "ID=debian", 'ID="ubuntu"', 'ID="debian"')
        ):
            raise SetupError("Цей скрипт підтримує Ubuntu та Debian із systemd.")
        if not Path("/run/systemd/system").is_dir():
            raise SetupError("Потрібен сервер із systemd.")
        phase = "dependencies"
        if not all(
            shutil.which(command) for command in ("ssh-keygen", "sudo", "useradd", "usermod")
        ):
            run_command(["/usr/bin/apt-get", "update"])
            run_command(
                [
                    "/usr/bin/apt-get",
                    "install",
                    "-y",
                    "openssh-server",
                    "sudo",
                    "passwd",
                    "ca-certificates",
                ]
            )
        phase = "account"
        account = ensure_account()
        phase = "files"
        for filename, encoded in config["files"].items():
            write_asset(
                filename,
                base64.b64decode(encoded),
                0o755
                if filename.endswith(
                    (
                        "ssh_dispatch.py",
                        "terminal-shell",
                        "serverwatch-db-export",
                        "serverwatch-docker-list",
                    )
                )
                else 0o644,
            )
        if config["enable_swap"]:
            write_asset(
                "/usr/local/sbin/serverwatch-swap", base64.b64decode(config["swap_helper"]), 0o755
            )
            rule = 'monitor ALL=(root) NOPASSWD: /usr/local/sbin/serverwatch-swap ""\n'
            if config.get("persistent_swap_helper"):
                write_asset(
                    "/usr/local/sbin/serverwatch-swap-persistent",
                    base64.b64decode(config["persistent_swap_helper"]),
                    0o755,
                )
                rule += (
                    'monitor ALL=(root) NOPASSWD: /usr/local/sbin/serverwatch-swap-persistent ""\n'
                )
            write_sudo_rules("serverwatch-monitor-swap", rule)
        rules = []
        if config.get("enable_docker_inventory"):
            rules.append('/usr/local/sbin/serverwatch-docker-list ""')
        if config.get("enable_reboot"):
            rules.append("/usr/bin/systemctl --no-block reboot")
        if config.get("enable_database_backups"):
            rules.extend(
                f"/usr/local/sbin/serverwatch-db-export {mode}"
                for mode in ("discover", "mysql", "mariadb", "postgresql")
            )
        if rules:
            write_sudo_rules(
                "serverwatch-monitor-operations",
                "monitor ALL=(root) NOPASSWD: " + ", ".join(rules) + "\n",
            )
        add_authorized_key(account, config["public_key"])
        if config.get("terminal_public_key"):
            add_authorized_key(
                ensure_terminal_account(), config["terminal_public_key"], terminal=True
            )
        phase = "ssh_config"
        host_key = host_public_key()
        notify(config, "completed", host_key=host_key)
        print("Ключ встановлено. Центральний worker перевіряє SSH і збір метрик.")
        print(
            "Поверніться до майстра ServerWatch: Telegram-код з’явиться після успішної перевірки."
        )
    except (SetupError, OSError, ValueError, subprocess.SubprocessError):
        if started:
            try:
                notify(config, "failed", error_code=phase)
            except SetupError:
                pass
        raise


def run(config):
    try:
        install(config)
    except SetupError as error:
        print("ПОМИЛКА: " + str(error))
        raise SystemExit(1) from None
    except (OSError, ValueError, subprocess.SubprocessError):
        print(
            "ПОМИЛКА: встановлення перервано. Приватні дані приховано; перевірте права та стан ОС."
        )
        raise SystemExit(1) from None
