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
            "monitoring_interval": self.monitoring_interval,
        }


class Setting(db.Model):
    __tablename__ = "settings"
    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.Integer, nullable=False)


class Job(db.Model):
    __tablename__ = "jobs"
    id = db.Column(db.Integer, primary_key=True)
    server_id = db.Column(db.Integer, db.ForeignKey("servers.id"), nullable=False, index=True)
    alert_id = db.Column(db.Integer, db.ForeignKey("alerts.id"), nullable=False, unique=True)
    requested_by = db.Column(db.BigInteger, nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False, index=True)
    result = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    finished_at = db.Column(db.DateTime)
