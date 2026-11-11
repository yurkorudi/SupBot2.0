from app.extensions import db

from . import utcnow


class Server(db.Model):
    __tablename__ = "servers"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    ip_address = db.Column(db.String(45), nullable=False)
    ssh_port = db.Column(db.Integer, default=22, nullable=False)
    ssh_username = db.Column(db.String(32), nullable=False)
    ssh_private_key_encrypted = db.Column(db.Text, nullable=False)
    ssh_public_key = db.Column(db.Text)
    ssh_key_fingerprint = db.Column(db.String(100))
    ssh_host_key = db.Column(db.Text)
    ssh_host_key_fingerprint = db.Column(db.String(100))
    installation_user = db.Column(db.String(32), default="root", nullable=False)
    installation_status = db.Column(db.String(32), default="READY", nullable=False, index=True)
    installation_token_hash = db.Column(db.String(64), unique=True)
    installation_token_expires_at = db.Column(db.DateTime)
    installation_token_used_at = db.Column(db.DateTime)
    installation_completed_at = db.Column(db.DateTime)
    installation_error = db.Column(db.String(300))
    telegram_authorized_at = db.Column(db.DateTime)
    swap_enabled = db.Column(db.Boolean, default=False, nullable=False)
    automation_enabled = db.Column(
        db.Boolean, default=True, nullable=False, server_default=db.true()
    )
    service_monitoring_enabled = db.Column(
        db.Boolean, default=True, nullable=False, server_default=db.true()
    )
    client_name = db.Column(db.String(100), default="", nullable=False)
    telegram_group_id = db.Column(db.BigInteger)
    authorization_code_hash = db.Column(db.String(64), unique=True)
    authorization_expires_at = db.Column(db.DateTime)
    is_authorized = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    monitoring_interval = db.Column(db.Integer, default=180, nullable=False)
    thresholds = db.Column(
        db.JSON,
        nullable=False,
        default=lambda: {
            "cpu_warning": 80,
            "cpu_critical": 95,
            "ram_warning": 80,
            "ram_critical": 90,
            "disk_warning": 85,
            "disk_critical": 95,
            "swap_warning": 80,
        },
    )
    services = db.Column(db.JSON, default=list, nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False)
    last_error = db.Column(db.String(200))
    last_success_at = db.Column(db.DateTime)
    next_check_at = db.Column(db.DateTime, default=utcnow, index=True, nullable=False)
    next_report_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    lease_until = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    def public_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "ip_address": self.ip_address,
            "status": self.status,
            "key_status": "Ключ збережено",
            "is_authorized": self.is_authorized,
            "installation_status": self.installation_status,
            "monitoring_interval": self.monitoring_interval,
        }


class ServerReportSchedule(db.Model):
    __tablename__ = "server_report_schedules"
    server_id = db.Column(db.Integer, db.ForeignKey("servers.id"), primary_key=True)
    mode = db.Column(db.String(12), nullable=False)
    timezone = db.Column(db.String(64), nullable=False)
    times = db.Column(db.JSON, nullable=False)
    weekdays = db.Column(db.JSON, nullable=False)
    next_at = db.Column(db.DateTime, index=True)
    manual_requested_at = db.Column(db.DateTime)


class ServerService(db.Model):
    __tablename__ = "server_services"
    id = db.Column(db.Integer, primary_key=True)
    server_id = db.Column(db.Integer, db.ForeignKey("servers.id"), nullable=False)
    kind = db.Column(db.String(12), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(12), nullable=False)
    monitored = db.Column(db.Boolean, nullable=False)
    checked_at = db.Column(db.DateTime, nullable=False)
    restarted_at = db.Column(db.DateTime)
    __table_args__ = (db.UniqueConstraint("server_id", "kind", "name", name="uq_server_service"),)


class ServerCpuRecovery(db.Model):
    __tablename__ = "server_cpu_recovery"
    server_id = db.Column(db.Integer, db.ForeignKey("servers.id"), primary_key=True)
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    previous_critical_at = db.Column(db.DateTime)
    due_at = db.Column(db.DateTime)
    started_at = db.Column(db.DateTime)
    verify_until = db.Column(db.DateTime)
    boot_id = db.Column(db.String(36))
    notification_id = db.Column(db.Integer)
    manual_actor_id = db.Column(db.Integer)


class ServerSwapAutomation(db.Model):
    __tablename__ = "server_swap_automation"
    server_id = db.Column(db.Integer, db.ForeignKey("servers.id"), primary_key=True)
    initial_enabled = db.Column(db.Boolean, nullable=False, default=False)
    critical_enabled = db.Column(db.Boolean, nullable=False, default=False)
    initial_queued = db.Column(db.Boolean, nullable=False, default=False)


class ServerBackup(db.Model):
    __tablename__ = "server_backups"
    server_id = db.Column(db.Integer, db.ForeignKey("servers.id"), primary_key=True)
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    requested_by = db.Column(db.Integer)
    next_at = db.Column(db.DateTime, index=True)
    status = db.Column(db.String(16), nullable=False, default="pending")
    result = db.Column(db.String(300))
    last_success_at = db.Column(db.DateTime)
    artifact = db.Column(db.String(64))
    size = db.Column(db.BigInteger)
    sha256 = db.Column(db.String(64))
    engines = db.Column(db.JSON)


class ServerTerminalAccess(db.Model):
    __tablename__ = "server_terminal_access"
    server_id = db.Column(db.Integer, db.ForeignKey("servers.id"), primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    username = db.Column(db.String(32), nullable=False)
    ssh_private_key_encrypted = db.Column(db.Text, nullable=False)
    ssh_public_key = db.Column(db.Text, nullable=False)


class Setting(db.Model):
    __tablename__ = "settings"
    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.Integer, nullable=False)


class Job(db.Model):
    __tablename__ = "jobs"
    id = db.Column(db.Integer, primary_key=True)
    server_id = db.Column(db.Integer, db.ForeignKey("servers.id"), nullable=False, index=True)
    alert_id = db.Column(db.Integer, db.ForeignKey("alerts.id"), unique=True)
    requested_by = db.Column(db.BigInteger, nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False, index=True)
    result = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    finished_at = db.Column(db.DateTime)
    source = db.Column(db.String(12), nullable=False, default="telegram", server_default="telegram")
    run_after = db.Column(db.DateTime, index=True)
    telegram_message_id = db.Column(db.BigInteger)
    telegram_group_id = db.Column(db.BigInteger)
