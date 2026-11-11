"""Store independent terminal credentials and per-server permissions."""

import sqlalchemy as sa
from alembic import op

revision = "7e8d452307a2"
down_revision = "e73c0f516911"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "server_terminal_access",
        sa.Column("server_id", sa.Integer(), sa.ForeignKey("servers.id"), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("username", sa.String(32), nullable=False),
        sa.Column("ssh_private_key_encrypted", sa.Text(), nullable=False),
        sa.Column("ssh_public_key", sa.Text(), nullable=False),
    )


def downgrade():
    op.drop_table("server_terminal_access")
