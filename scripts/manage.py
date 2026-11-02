"""Run local administrative commands with the same environment as systemd."""

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = Path("/etc/serverwatch")
sys.path.insert(0, str(ROOT))


def read_environment(migration=False):
    path = DIRECTORY / "serverwatch.env"
    if not path.is_file():
        raise ValueError("Відсутній serverwatch.env.")
    values = {key: value for key, value in dotenv_values(path).items() if value is not None}
    if migration:
        path = DIRECTORY / "migration.env"
        if not path.is_file():
            raise ValueError("Відсутній migration.env.")
        values.update(
            {key: value for key, value in dotenv_values(path).items() if value is not None}
        )
    return values


def drop_root():
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        import pwd

        user = pwd.getpwnam("serverwatch")
        os.initgroups(user.pw_name, user.pw_gid)
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)
        os.environ["HOME"] = user.pw_dir


async def telegram_ids():
    from aiogram import Bot

    async with Bot(os.environ["TELEGRAM_BOT_TOKEN"]) as bot:
        updates = await bot.get_updates(timeout=0)
        for update in updates:
            if update.message and update.message.from_user:
                sender = update.message.from_user
                print(f"User ID: {sender.id}; username: {sender.username or '—'}")
        if not updates:
            print("Немає оновлень. Надішліть приватне повідомлення боту й повторіть команду.")


def database_check(status=False):
    from sqlalchemy import inspect, text

    from app import create_app
    from app.extensions import db

    with create_app().app_context():
        with db.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            print("MySQL: OK; host=127.0.0.1; database=serverwatch; user=" + db.engine.url.username)
            if not status:
                return
            tables = inspect(connection).get_table_names()
            print("Таблиці: " + ", ".join(tables))
            for table in ("users", "servers", "server_metrics", "server_hourly_metrics", "alerts"):
                if table in tables:
                    count = connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
                    print(f"{table}: {count}")
            if "alembic_version" in tables:
                print(
                    "Міграція: "
                    + str(
                        connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                    )
                )
            if "servers" in tables:
                for row in connection.execute(
                    text("SELECT id, status, last_success_at FROM servers ORDER BY id LIMIT 20")
                ):
                    print(
                        f"server_id={row.id}; status={row.status}; last_success_at={row.last_success_at}"
                    )
            if "server_metrics" in tables:
                print(
                    "Остання метрика UTC: "
                    + str(
                        connection.execute(
                            text("SELECT MAX(collected_at) FROM server_metrics")
                        ).scalar()
                    )
                )
            if "outbox" in tables:
                queued = connection.execute(
                    text("SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL")
                ).scalar_one()
                sent = connection.execute(
                    text("SELECT COUNT(*) FROM outbox WHERE sent_at IS NOT NULL")
                ).scalar_one()
                print(f"Telegram queued={queued}; sent={sent}")
            if "admin_actions" in tables:
                for row in connection.execute(
                    text(
                        "SELECT action, result, created_at FROM admin_actions ORDER BY id DESC LIMIT 10"
                    )
                ):
                    print(f"{row.created_at}: {row.action}; {row.result}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("check-db", "status", "upgrade", "create-admin", "reset-totp", "telegram-id"),
    )
    parser.add_argument("--username")
    args = parser.parse_args()
    try:
        os.environ.update(read_environment(migration=args.command == "upgrade"))
        drop_root()
        os.chdir(ROOT)
        if args.command in ("check-db", "status"):
            database_check(status=args.command == "status")
        elif args.command == "telegram-id":
            asyncio.run(telegram_ids())
        else:
            command = [sys.executable, "-m", "flask", "--app", "run"]
            if args.command == "upgrade":
                command.extend(["db", "upgrade"])
            elif args.command == "reset-totp":
                if not args.username:
                    parser.error("Для reset-totp задайте --username.")
                command.extend(["reset-totp", args.username])
            else:
                command.append("create-admin")
            return subprocess.call(command)
    except Exception as error:
        print(
            f"Команда не виконана ({type(error).__name__}). Перевірте права, environment та MySQL; секрети приховано.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
