from app.extensions import db

from . import utcnow


class ServerMetric(db.Model):
    __tablename__ = "server_metrics"
    id = db.Column(db.Integer, primary_key=True)
    server_id = db.Column(db.Integer, db.ForeignKey("servers.id"), nullable=False)
    cpu_usage = db.Column(db.Float, nullable=False)
    ram_percent = db.Column(db.Float, nullable=False)
    swap_percent = db.Column(db.Float, nullable=False)
    disk_percent = db.Column(db.Float, nullable=False)
    ram_total = db.Column(db.BigInteger, nullable=False)
    ram_used = db.Column(db.BigInteger, nullable=False)
    swap_total = db.Column(db.BigInteger, nullable=False)
    swap_used = db.Column(db.BigInteger, nullable=False)
    disk_total = db.Column(db.BigInteger, nullable=False)
    disk_used = db.Column(db.BigInteger, nullable=False)
    details = db.Column(db.JSON, nullable=False)
    collected_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    __table_args__ = (db.Index("ix_metrics_server_time", "server_id", "collected_at"),)


class HourlyMetric(db.Model):
    __tablename__ = "server_hourly_metrics"
    id = db.Column(db.Integer, primary_key=True)
    server_id = db.Column(db.Integer, db.ForeignKey("servers.id"), nullable=False)
    hour = db.Column(db.DateTime, nullable=False)
    cpu_avg = db.Column(db.Float, nullable=False)
    cpu_max = db.Column(db.Float, nullable=False)
    ram_avg = db.Column(db.Float, nullable=False)
    ram_max = db.Column(db.Float, nullable=False)
    swap_avg = db.Column(db.Float, nullable=False)
    swap_max = db.Column(db.Float, nullable=False)
    disk_avg = db.Column(db.Float, nullable=False)
    disk_max = db.Column(db.Float, nullable=False)
    __table_args__ = (db.UniqueConstraint("server_id", "hour", name="uq_hourly_server_hour"),)


class ServerLog(db.Model):
    __tablename__ = "server_logs"
    id = db.Column(db.Integer, primary_key=True)
    server_id = db.Column(db.Integer, db.ForeignKey("servers.id"), nullable=False)
    service_name = db.Column(db.String(100), nullable=False)
    log_level = db.Column(db.String(20), nullable=False)
    message = db.Column(db.String(300), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    __table_args__ = (db.Index("ix_logs_server_time", "server_id", "created_at"),)
