"""Initial ServerWatch schema

Revision ID: c99481d201d8
Revises:
Create Date: 2026-11-02 02:08:25.466427

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c99481d201d8"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "login_attempts",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("blocked_until", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "servers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False),
        sa.Column("ssh_username", sa.String(length=32), nullable=False),
        sa.Column("ssh_private_key_encrypted", sa.Text(), nullable=False),
        sa.Column("client_name", sa.String(length=100), nullable=False),
        sa.Column("telegram_group_id", sa.BigInteger(), nullable=True),
        sa.Column("authorization_code_hash", sa.String(length=64), nullable=True),
        sa.Column("authorization_expires_at", sa.DateTime(), nullable=True),
        sa.Column("is_authorized", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("monitoring_interval", sa.Integer(), nullable=False),
        sa.Column("thresholds", sa.JSON(), nullable=False),
        sa.Column("services", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("last_error", sa.String(length=200), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("next_check_at", sa.DateTime(), nullable=False),
        sa.Column("next_report_at", sa.DateTime(), nullable=False),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("authorization_code_hash"),
    )
    with op.batch_alter_table("servers", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_servers_next_check_at"), ["next_check_at"], unique=False
        )

    op.create_table(
        "settings",
        sa.Column("key", sa.String(length=60), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("totp_secret_encrypted", sa.Text(), nullable=False),
        sa.Column("totp_enabled", sa.Boolean(), nullable=False),
        sa.Column("last_totp_step", sa.BigInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_table(
        "admin_actions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("admin_id", sa.String(length=80), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("result", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["servers.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("admin_actions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_admin_actions_created_at"), ["created_at"], unique=False
        )

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("alert_type", sa.String(length=30), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("acknowledged_by", sa.BigInteger(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("notified_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["servers.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.create_index("ix_alert_server_status", ["server_id", "status"], unique=False)

    op.create_table(
        "server_hourly_metrics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("hour", sa.DateTime(), nullable=False),
        sa.Column("cpu_avg", sa.Float(), nullable=False),
        sa.Column("cpu_max", sa.Float(), nullable=False),
        sa.Column("ram_avg", sa.Float(), nullable=False),
        sa.Column("ram_max", sa.Float(), nullable=False),
        sa.Column("swap_avg", sa.Float(), nullable=False),
        sa.Column("swap_max", sa.Float(), nullable=False),
        sa.Column("disk_avg", sa.Float(), nullable=False),
        sa.Column("disk_max", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["servers.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("server_id", "hour", name="uq_hourly_server_hour"),
    )
    op.create_table(
        "server_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("service_name", sa.String(length=100), nullable=False),
        sa.Column("log_level", sa.String(length=20), nullable=False),
        sa.Column("message", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["servers.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("server_logs", schema=None) as batch_op:
        batch_op.create_index("ix_logs_server_time", ["server_id", "created_at"], unique=False)

    op.create_table(
        "server_metrics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("cpu_usage", sa.Float(), nullable=False),
        sa.Column("ram_percent", sa.Float(), nullable=False),
        sa.Column("swap_percent", sa.Float(), nullable=False),
        sa.Column("disk_percent", sa.Float(), nullable=False),
        sa.Column("ram_total", sa.BigInteger(), nullable=False),
        sa.Column("ram_used", sa.BigInteger(), nullable=False),
        sa.Column("swap_total", sa.BigInteger(), nullable=False),
        sa.Column("swap_used", sa.BigInteger(), nullable=False),
        sa.Column("disk_total", sa.BigInteger(), nullable=False),
        sa.Column("disk_used", sa.BigInteger(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("collected_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["servers.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("server_metrics", schema=None) as batch_op:
        batch_op.create_index("ix_metrics_server_time", ["server_id", "collected_at"], unique=False)

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("alert_id", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("result", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["servers.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alert_id"),
    )
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_jobs_server_id"), ["server_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_jobs_status"), ["status"], unique=False)

    op.create_table(
        "outbox",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("alert_id", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("outbox", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_outbox_available_at"), ["available_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_outbox_sent_at"), ["sent_at"], unique=False)


def downgrade():
    with op.batch_alter_table("outbox", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_outbox_sent_at"))
        batch_op.drop_index(batch_op.f("ix_outbox_available_at"))

    op.drop_table("outbox")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_jobs_status"))
        batch_op.drop_index(batch_op.f("ix_jobs_server_id"))

    op.drop_table("jobs")
    with op.batch_alter_table("server_metrics", schema=None) as batch_op:
        batch_op.drop_index("ix_metrics_server_time")

    op.drop_table("server_metrics")
    with op.batch_alter_table("server_logs", schema=None) as batch_op:
        batch_op.drop_index("ix_logs_server_time")

    op.drop_table("server_logs")
    op.drop_table("server_hourly_metrics")
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.drop_index("ix_alert_server_status")

    op.drop_table("alerts")
    with op.batch_alter_table("admin_actions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_admin_actions_created_at"))

    op.drop_table("admin_actions")
    op.drop_table("users")
    op.drop_table("settings")
    with op.batch_alter_table("servers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_servers_next_check_at"))

    op.drop_table("servers")
    op.drop_table("login_attempts")
