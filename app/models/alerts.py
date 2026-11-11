from app.extensions import db

from . import utcnow


class Alert(db.Model):
    __tablename__ = "alerts"
    id = db.Column(db.Integer, primary_key=True)
    server_id = db.Column(db.Integer, db.ForeignKey("servers.id"), nullable=False)
    alert_type = db.Column(db.String(30), nullable=False)
    severity = db.Column(db.String(20), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="open", nullable=False)
    acknowledged_by = db.Column(db.BigInteger)
    acknowledged_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    notified_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    resolved_at = db.Column(db.DateTime)
    __table_args__ = (db.Index("ix_alert_server_status", "server_id", "status"),)


class Outbox(db.Model):
    __tablename__ = "outbox"
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.BigInteger, nullable=False)
    message = db.Column(db.Text, nullable=False)
    alert_id = db.Column(db.Integer, db.ForeignKey("alerts.id"))
    edit_message_id = db.Column(db.BigInteger)
    attempts = db.Column(db.Integer, default=0, nullable=False)
    available_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    sent_at = db.Column(db.DateTime, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class AdminAction(db.Model):
    __tablename__ = "admin_actions"
    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.String(80), nullable=False)
    server_id = db.Column(db.Integer, db.ForeignKey("servers.id"))
    action = db.Column(db.String(80), nullable=False)
    result = db.Column(db.String(300), nullable=False)
    source = db.Column(db.String(12))
    status = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    __table_args__ = (db.Index("ix_admin_actions_server_created", "server_id", "created_at"),)
