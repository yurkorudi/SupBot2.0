"""Add per-server Telegram report schedules."""

import sqlalchemy as sa
from alembic import op

revision = "f9b3c61de47a"
down_revision = "4e2c9d8f610a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "server_report_schedules",
        sa.Column("server_id", sa.Integer(), sa.ForeignKey("servers.id"), primary_key=True),
        sa.Column("mode", sa.String(12), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("times", sa.JSON(), nullable=False),
        sa.Column("weekdays", sa.JSON(), nullable=False),
        sa.Column("next_at", sa.DateTime()),
        sa.Column("manual_requested_at", sa.DateTime()),
    )
    op.create_index("ix_server_report_schedules_next_at", "server_report_schedules", ["next_at"])


def downgrade():
    op.drop_table("server_report_schedules")
