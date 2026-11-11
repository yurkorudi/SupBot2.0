"""Persist per-server Swap switches and scheduled jobs."""

import sqlalchemy as sa
from alembic import op

revision = "a4f72c18c46d"
down_revision = "b6c742151da3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "server_swap_automation",
        sa.Column("server_id", sa.Integer(), sa.ForeignKey("servers.id"), primary_key=True),
        sa.Column("initial_enabled", sa.Boolean(), nullable=False),
        sa.Column("critical_enabled", sa.Boolean(), nullable=False),
        sa.Column("initial_queued", sa.Boolean(), nullable=False),
    )
    with op.batch_alter_table("jobs") as batch:
        batch.alter_column("alert_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(
            sa.Column("source", sa.String(12), nullable=False, server_default="telegram")
        )
        batch.add_column(sa.Column("run_after", sa.DateTime()))
        batch.add_column(sa.Column("telegram_message_id", sa.BigInteger()))
        batch.add_column(sa.Column("telegram_group_id", sa.BigInteger()))
        batch.create_index("ix_jobs_run_after", ["run_after"])
    op.add_column("outbox", sa.Column("edit_message_id", sa.BigInteger()))


def downgrade():
    with op.batch_alter_table("outbox") as batch:
        batch.drop_column("edit_message_id")
    op.execute(sa.text("DELETE FROM jobs WHERE alert_id IS NULL"))
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_run_after")
        batch.drop_column("run_after")
        batch.drop_column("telegram_message_id")
        batch.drop_column("telegram_group_id")
        batch.drop_column("source")
        batch.alter_column("alert_id", existing_type=sa.Integer(), nullable=False)
    op.drop_table("server_swap_automation")
