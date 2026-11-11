"""Store CPU recovery deadlines and reboot verification state."""

import sqlalchemy as sa
from alembic import op

revision = "b6c742151da3"
down_revision = "6b8f3d58e15d"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "server_cpu_recovery",
        sa.Column("server_id", sa.Integer(), sa.ForeignKey("servers.id"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("previous_critical_at", sa.DateTime()),
        sa.Column("due_at", sa.DateTime()),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("verify_until", sa.DateTime()),
        sa.Column("boot_id", sa.String(36)),
        sa.Column("notification_id", sa.Integer()),
        sa.Column("manual_actor_id", sa.Integer()),
    )


def downgrade():
    op.drop_table("server_cpu_recovery")
