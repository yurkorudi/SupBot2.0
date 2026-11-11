"""Add the per-VPS automation and service-monitoring switches."""

import sqlalchemy as sa
from alembic import op

revision = "35166c40bfc0"
down_revision = "7e8d452307a2"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("automation_enabled", "service_monitoring_enabled"):
        op.add_column(
            "servers", sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.true())
        )


def downgrade():
    with op.batch_alter_table("servers") as batch:
        batch.drop_column("service_monitoring_enabled")
        batch.drop_column("automation_enabled")
