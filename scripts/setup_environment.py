"""Generate first-install secrets without printing them or passing them as CLI arguments."""

import argparse
import getpass
import os
import re
import secrets
from pathlib import Path
from urllib.parse import quote

from cryptography.fernet import Fernet

DIRECTORY = Path("/etc/serverwatch")


def database_url(username, password):
    return f"mysql+pymysql://{username}:{quote(password, safe='')}@127.0.0.1/serverwatch?charset=utf8mb4"


def build_files(access, token, admin_id, public_base_url=""):
    runtime_password = secrets.token_hex(32)
    migration_password = secrets.token_hex(32)
    values = {
        "SECRET_KEY": secrets.token_hex(32),
        "PUBLIC_BASE_URL": public_base_url,
        "ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "DATABASE_URL": database_url("serverwatch", runtime_password),
        "TELEGRAM_BOT_TOKEN": token,
        "TELEGRAM_ADMIN_ID": str(admin_id),
        "SSH_KNOWN_HOSTS": "/etc/serverwatch/known_hosts",
        "SSH_TIMEOUT": "12",
        "MONITOR_CONCURRENCY": "2",
        "SESSION_COOKIE_SECURE": "true" if access == "https" else "false",
        "AI_ENABLED": "false",
        "AI_URL": "http://127.0.0.1:11434/api/generate",
        "AI_MODEL": "llama3.2:1b",
    }
    sql = f"""CREATE DATABASE serverwatch CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'serverwatch'@'127.0.0.1' IDENTIFIED BY '{runtime_password}';
GRANT SELECT, INSERT, UPDATE, DELETE ON serverwatch.* TO 'serverwatch'@'127.0.0.1';
CREATE USER 'serverwatch_migrate'@'127.0.0.1' IDENTIFIED BY '{migration_password}';
GRANT ALL PRIVILEGES ON serverwatch.* TO 'serverwatch_migrate'@'127.0.0.1';
"""
    return {
        "serverwatch.env": "".join(f"{key}={value}\n" for key, value in values.items()),
        "migration.env": "DATABASE_URL="
        + database_url("serverwatch_migrate", migration_password)
        + "\n",
        "bootstrap.sql": sql,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--access", choices=("tunnel", "https"), required=True)
    args = parser.parse_args()
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        parser.error("Запустіть через sudo для встановлення root-owned файлів.")
    import grp

    filenames = ("serverwatch.env", "migration.env", "bootstrap.sql")
    if any((DIRECTORY / name).exists() for name in filenames):
        parser.error("Конфігурація вже існує. Повторна генерація секретів заборонена.")
    token = getpass.getpass("Токен BotFather (приховано): ").strip()
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        parser.error("Потрібен токен BotFather без пробілів.")
    raw_id = input("Числовий Telegram User ID (Enter, якщо ще невідомий): ").strip()
    if raw_id and (not raw_id.isascii() or not raw_id.isdigit() or int(raw_id) <= 0):
        parser.error("User ID має бути додатним числом.")
    base_url = input("Публічна HTTPS-адреса ServerWatch (Enter — налаштувати пізніше): ").strip()
    if base_url and (not base_url.startswith("https://") or any(c.isspace() for c in base_url)):
        parser.error("Потрібна HTTPS-адреса без пробілів.")
    group_id = grp.getgrnam("serverwatch").gr_gid
    os.umask(0o077)
    if DIRECTORY.is_symlink():
        parser.error("Каталог конфігурації не повинен бути symlink.")
    DIRECTORY.mkdir(mode=0o750, exist_ok=True)
    os.chown(DIRECTORY, 0, group_id)
    os.chmod(DIRECTORY, 0o750)
    for name, content in build_files(args.access, token, int(raw_id or 0), base_url).items():
        path = DIRECTORY / name
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(content)
        os.chown(path, 0, group_id if name == "serverwatch.env" else 0)
        os.chmod(path, 0o640 if name == "serverwatch.env" else 0o600)
    print("Створено serverwatch.env (0640), migration.env і bootstrap.sql (0600).")
    print("Секрети не виводилися. Наступний крок: імпорт bootstrap.sql через sudo mysql.")


if __name__ == "__main__":
    main()
