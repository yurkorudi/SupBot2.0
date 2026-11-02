"""Recreate MySQL users on a new host using the original restored environment."""

import os
import sys
from pathlib import Path

import pymysql
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.manage import read_environment  # noqa: E402


def credentials():
    result = []
    for migration, expected in ((False, "serverwatch"), (True, "serverwatch_migrate")):
        url = make_url(read_environment(migration)["DATABASE_URL"])
        if url.username != expected or url.host != "127.0.0.1" or url.database != "serverwatch":
            raise ValueError("Неочікувана конфігурація БД.")
        if not url.password:
            raise ValueError("Відсутній пароль БД.")
        result.append((expected, url.password))
    return result


def main():
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise PermissionError("Потрібен root для локального MySQL socket.")
    users = credentials()
    with pymysql.connect(user="root", unix_socket="/var/run/mysqld/mysqld.sock") as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM mysql.user WHERE User IN ('serverwatch','serverwatch_migrate')"
            )
            if cursor.fetchone()[0]:
                raise ValueError("Ролі вже існують. Зміна паролів заборонена.")
            for username, password in users:
                # Usernames are fixed above; passwords are bound SQL parameters.
                cursor.execute(
                    f"CREATE USER '{username}'@'127.0.0.1' IDENTIFIED BY %s", (password,)
                )
                privileges = (
                    "ALL PRIVILEGES"
                    if username.endswith("_migrate")
                    else "SELECT, INSERT, UPDATE, DELETE"
                )
                cursor.execute(f"GRANT {privileges} ON serverwatch.* TO '{username}'@'127.0.0.1'")
        connection.commit()
    print("Ролі відновлено з початковими паролями. Секрети не виводилися.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            f"Відновлення ролей не виконане ({type(error).__name__}); перевірте root socket, наявні ролі та конфігурацію.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
