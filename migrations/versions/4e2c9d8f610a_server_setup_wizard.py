"""Add generated SSH keys, pinned hosts and one-time installation state."""

import sqlalchemy as sa
from alembic import op

revision = "4e2c9d8f610a"
down_revision = "c99481d201d8"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("servers") as batch:
        batch.add_column(sa.Column("ssh_public_key", sa.Text()))
        batch.add_column(sa.Column("ssh_key_fingerprint", sa.String(100)))
        batch.add_column(sa.Column("ssh_host_key", sa.Text()))
        batch.add_column(sa.Column("ssh_host_key_fingerprint", sa.String(100)))
        batch.add_column(
            sa.Column("installation_user", sa.String(32), nullable=False, server_default="root")
        )
        batch.add_column(
            sa.Column("installation_status", sa.String(32), nullable=False, server_default="READY")
        )
        batch.add_column(sa.Column("installation_token_hash", sa.String(64)))
        batch.add_column(sa.Column("installation_token_expires_at", sa.DateTime()))
        batch.add_column(sa.Column("installation_token_used_at", sa.DateTime()))
        batch.add_column(sa.Column("installation_completed_at", sa.DateTime()))
        batch.add_column(sa.Column("installation_error", sa.String(300)))
        batch.add_column(sa.Column("telegram_authorized_at", sa.DateTime()))
        batch.add_column(
            sa.Column("swap_enabled", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.create_unique_constraint("uq_servers_installation_token", ["installation_token_hash"])
        batch.create_index("ix_servers_installation_status", ["installation_status"])
    op.execute(
        sa.text(
            "UPDATE servers SET installation_status = CASE WHEN is_active = 0 "
            "THEN 'DISABLED' WHEN is_authorized = 1 THEN 'ACTIVE' ELSE 'READY' END"
        )
    )


def downgrade():
    with op.batch_alter_table("servers") as batch:
        batch.drop_index("ix_servers_installation_status")
        batch.drop_constraint("uq_servers_installation_token", type_="unique")
        for column in (
            "swap_enabled",
            "telegram_authorized_at",
            "installation_error",
            "installation_completed_at",
            "installation_token_used_at",
            "installation_token_expires_at",
            "installation_token_hash",
            "installation_status",
            "installation_user",
            "ssh_host_key_fingerprint",
            "ssh_host_key",
            "ssh_key_fingerprint",
            "ssh_public_key",
        ):
            batch.drop_column(column)
