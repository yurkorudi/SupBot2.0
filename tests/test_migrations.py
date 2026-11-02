import importlib
import io
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect

from app.extensions import db
from app.models import Server
from app.services.encryption import encrypt


def test_migration_roundtrip(app):
    directory = str(Path(__file__).parents[1] / "migrations")
    db.drop_all()
    upgrade(directory=directory)
    tables = inspect(db.engine).get_table_names()
    assert "servers" in tables and "outbox" in tables and "server_hourly_metrics" in tables
    downgrade(directory=directory, revision="base")
    assert inspect(db.engine).get_table_names() == ["alembic_version"]


def test_wizard_migration_preserves_legacy_servers(app):
    directory = str(Path(__file__).parents[1] / "migrations")
    db.drop_all()
    upgrade(directory=directory)
    encrypted = encrypt("LEGACY-KEY")
    for name, active, authorized in (
        ("active", True, True),
        ("unbound", True, False),
        ("disabled", False, True),
    ):
        db.session.add(
            Server(
                name=name,
                ip_address="8.8.8.8",
                ssh_username="serverwatch",
                ssh_private_key_encrypted=encrypted,
                is_active=active,
                is_authorized=authorized,
                telegram_group_id=-100999,
            )
        )
    db.session.commit()
    # Exercise an upgrade containing real legacy rows, not only empty tables.
    downgrade(directory=directory, revision="c99481d201d8")
    upgrade(directory=directory)
    db.session.expire_all()
    rows = db.session.scalars(db.select(Server).order_by(Server.id)).all()
    assert [row.installation_status for row in rows] == ["ACTIVE", "READY", "DISABLED"]
    assert all(row.ssh_private_key_encrypted == encrypted for row in rows)
    assert all(
        row.telegram_group_id == -100999 and row.ssh_username == "serverwatch" for row in rows
    )
    assert all(row.ssh_public_key is None and row.ssh_host_key is None for row in rows)


def test_wizard_mysql_ddl_compiles_without_database_connection():
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="mysql", opts={"as_sql": True, "output_buffer": output}
    )
    revision = importlib.import_module("migrations.versions.4e2c9d8f610a_server_setup_wizard")
    with Operations.context(context):
        revision.upgrade()
    sql = output.getvalue()
    assert "ALTER TABLE servers ADD COLUMN ssh_public_key TEXT" in sql
    assert "CREATE INDEX ix_servers_installation_status" in sql
    assert "UNIQUE (installation_token_hash)" in sql
    assert "UPDATE servers SET installation_status" in sql
