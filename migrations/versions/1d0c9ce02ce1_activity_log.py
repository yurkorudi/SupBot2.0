"""Record source and status in the existing activity journal."""

import sqlalchemy as sa
from alembic import op

revision = "1d0c9ce02ce1"
down_revision = "f9b3c61de47a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("admin_actions", sa.Column("source", sa.String(12)))
    op.add_column("admin_actions", sa.Column("status", sa.String(20)))
    op.create_index("ix_admin_actions_server_created", "admin_actions", ["server_id", "created_at"])


def downgrade():
    op.drop_index("ix_admin_actions_server_created", table_name="admin_actions")
    with op.batch_alter_table("admin_actions") as batch:
        batch.drop_column("status")
        batch.drop_column("source")
