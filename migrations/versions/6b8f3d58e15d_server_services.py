"""Store services discovered on each VPS."""

import sqlalchemy as sa
from alembic import op

revision = "6b8f3d58e15d"
down_revision = "1d0c9ce02ce1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "server_services",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("server_id", sa.Integer(), sa.ForeignKey("servers.id"), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("monitored", sa.Boolean(), nullable=False),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.Column("restarted_at", sa.DateTime()),
        sa.UniqueConstraint("server_id", "kind", "name", name="uq_server_service"),
    )


def downgrade():
    op.drop_table("server_services")
