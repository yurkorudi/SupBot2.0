from pathlib import Path

from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect

from app.extensions import db


def test_migration_roundtrip(app):
    directory = str(Path(__file__).parents[1] / "migrations")
    db.drop_all()
    upgrade(directory=directory)
    tables = inspect(db.engine).get_table_names()
    assert "servers" in tables and "outbox" in tables and "server_hourly_metrics" in tables
    downgrade(directory=directory, revision="base")
    assert inspect(db.engine).get_table_names() == ["alembic_version"]
