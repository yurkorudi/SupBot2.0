from datetime import timedelta

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import login_required
from sqlalchemy import func

from app.extensions import db
from app.models import AdminAction, Alert, HourlyMetric, Server, ServerMetric, utcnow

bp = Blueprint("dashboard", __name__)


def latest_query():
    latest = (
        db.select(ServerMetric.server_id, func.max(ServerMetric.id).label("id"))
        .group_by(ServerMetric.server_id)
        .subquery()
    )
    return db.select(ServerMetric).join(latest, ServerMetric.id == latest.c.id)


@bp.get("/")
@login_required
def index():
    counts = dict(
        db.session.execute(
            db.select(Server.status, func.count(Server.id)).group_by(Server.status)
        ).all()
    )
    current_metrics = (
        latest_query()
        .join(Server, Server.id == ServerMetric.server_id)
        .where(Server.is_active.is_(True), Server.status.in_(("online", "warning")))
    )
    latest = current_metrics.subquery()
    stats = db.session.execute(
        db.select(
            func.avg(latest.c.cpu_usage),
            func.sum(latest.c.ram_used) * 100.0 / func.nullif(func.sum(latest.c.ram_total), 0),
            func.sum(latest.c.disk_used),
        )
    ).one()
    alerts = db.session.scalars(db.select(Alert).order_by(Alert.created_at.desc()).limit(8)).all()
    page = db.paginate(
        db.select(Server).order_by(Server.name, Server.id), per_page=20, max_per_page=20
    )
    metrics = {
        metric.server_id: metric
        for metric in db.session.scalars(
            latest_query().where(ServerMetric.server_id.in_([server.id for server in page.items]))
        )
    }
    critical = set(
        db.session.scalars(
            db.select(Alert.server_id)
            .where(
                Alert.severity == "critical",
                Alert.resolved_at.is_(None),
                Alert.alert_type != "connection",
            )
            .distinct()
        )
    )
    active_services = sum(
        sum(state == "active" for state in (details or {}).get("services", {}).values())
        for details in db.session.scalars(db.select(latest.c.details))
    )
    last_backup = db.session.scalar(
        db.select(AdminAction)
        .where(AdminAction.action == "database_backup", AdminAction.status.in_(("done", "failed")))
        .order_by(AdminAction.created_at.desc(), AdminAction.id.desc())
        .limit(1)
    )
    return render_template(
        "dashboard.html",
        counts=counts,
        total=sum(counts.values()),
        stats=stats,
        alerts=alerts,
        servers=page.items,
        page=page,
        metrics=metrics,
        critical=critical,
        active_services=active_services,
        last_backup=last_backup,
    )


@bp.get("/api/analytics/<int:server_id>")
@login_required
def analytics(server_id):
    db.get_or_404(Server, server_id)
    period = request.args.get("period", "24h")
    if period not in ("24h", "7d", "30d"):
        abort(400)
    result = {
        "labels": [],
        "cpu": [],
        "ram": [],
        "swap": [],
        "disk": [],
        "cpu_max": [],
        "ram_max": [],
        "swap_max": [],
        "disk_max": [],
    }
    if period == "24h":
        rows = db.session.scalars(
            db.select(ServerMetric)
            .where(
                ServerMetric.server_id == server_id,
                ServerMetric.collected_at >= utcnow() - timedelta(hours=24),
            )
            .order_by(ServerMetric.collected_at)
            .limit(1441)
        ).all()
        for row in rows:
            result["labels"].append(row.collected_at.isoformat() + "Z")
            for key, field in (
                ("cpu", "cpu_usage"),
                ("ram", "ram_percent"),
                ("swap", "swap_percent"),
                ("disk", "disk_percent"),
            ):
                result[key].append(getattr(row, field))
                result[key + "_max"].append(getattr(row, field))
    else:
        days = 7 if period == "7d" else 30
        rows = db.session.scalars(
            db.select(HourlyMetric)
            .where(
                HourlyMetric.server_id == server_id,
                HourlyMetric.hour >= utcnow() - timedelta(days=days),
            )
            .order_by(HourlyMetric.hour)
            .limit(721)
        ).all()
        for row in rows:
            result["labels"].append(row.hour.isoformat() + "Z")
            for key in ("cpu", "ram", "swap", "disk"):
                result[key].append(getattr(row, key + "_avg"))
                result[key + "_max"].append(getattr(row, key + "_max"))
    result["summary"] = {
        key: {
            "avg": sum(result[key]) / len(result[key]) if result[key] else 0,
            "max": max(result[key + "_max"], default=0),
        }
        for key in ("cpu", "ram", "swap", "disk")
    }
    return jsonify(result)


@bp.get("/alerts")
@login_required
def alerts():
    page = db.paginate(
        db.select(Alert).order_by(Alert.created_at.desc()), per_page=25, max_per_page=25
    )
    return render_template("alerts.html", page=page)
