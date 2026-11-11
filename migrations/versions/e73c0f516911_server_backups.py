"""Track remote database backup schedules, requests, and verified artifacts."""

import sqlalchemy as sa
from alembic import op

revision = "e73c0f516911"
down_revision = "a4f72c18c46d"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "server_backups",
        sa.Column("server_id", sa.Integer(), sa.ForeignKey("servers.id"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("requested_by", sa.Integer()),
        sa.Column("next_at", sa.DateTime()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result", sa.String(300)),
        sa.Column("last_success_at", sa.DateTime()),
        sa.Column("artifact", sa.String(64)),
        sa.Column("size", sa.BigInteger()),
        sa.Column("sha256", sa.String(64)),
        sa.Column("engines", sa.JSON()),
    )
    op.create_index("ix_server_backups_next_at", "server_backups", ["next_at"])


def downgrade():
    op.drop_table("server_backups")
