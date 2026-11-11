from datetime import date, timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import AdminAction, Outbox, Server, ServerCpuRecovery, Setting
from app.services.alert_manager import setting
from app.services.security import audit
from app.services.validation import integer

bp = Blueprint("settings", __name__)


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        try:
            values = {
                "alert_repeat": integer(
                    request.form.get("alert_repeat"), 300, 86400, "Повтор аварії"
                ),
                "report_interval": integer(
                    request.form.get("report_interval"), 3600, 86400, "Інтервал звіту"
                ),
            }
            if "automation_present" in request.form:
                values["automation_enabled"] = int(request.form.get("automation_enabled") == "on")
                if not values["automation_enabled"]:
                    db.session.execute(
                        db.update(ServerCpuRecovery).values(
                            previous_critical_at=None, due_at=None, notification_id=None
                        )
                    )
            for key, value in values.items():
                item = db.session.get(Setting, key)
                if item:
                    item.value = value
                else:
                    db.session.add(Setting(key=key, value=value))
            audit(current_user.id, "settings_updated", "Інтервали оновлено")
            db.session.commit()
            flash("Налаштування збережено.")
            return redirect(url_for("settings.index"))
        except ValueError as error:
            flash(str(error), "error")
    queued = db.session.scalar(db.select(db.func.count(Outbox.id)).where(Outbox.sent_at.is_(None)))
    return render_template(
        "settings.html",
        repeat=setting("alert_repeat", 3600),
        interval=setting("report_interval", 14400),
        queued=queued,
        bot_configured=bool(current_app.config["TELEGRAM_BOT_TOKEN"]),
        admin_id=current_app.config["TELEGRAM_ADMIN_ID"],
        automation_enabled=setting("automation_enabled", 0),
    )


@bp.get("/security")
@bp.get("/activity")
@login_required
def security():
    query = db.select(AdminAction)
    filters = {}
    for field in ("server_id", "action", "status", "date"):
        value = request.args.get(field, "").strip()
        if not value:
            continue
        filters[field] = value
        if field == "server_id":
            try:
                query = query.where(AdminAction.server_id == integer(value, 1, 2**31 - 1, "VPS"))
            except ValueError:
                abort(400)
        elif field == "date":
            try:
                day = date.fromisoformat(value)
                tomorrow = day + timedelta(days=1)
            except (ValueError, OverflowError):
                abort(400)
            query = query.where(AdminAction.created_at >= day, AdminAction.created_at < tomorrow)
        else:
            query = query.where(getattr(AdminAction, field) == value)
    page = db.paginate(
        query.order_by(AdminAction.created_at.desc(), AdminAction.id.desc()),
        per_page=25,
        max_per_page=25,
    )
    servers = db.session.scalars(db.select(Server).order_by(Server.name)).all()
    actions = db.session.scalars(
        db.select(AdminAction.action).distinct().order_by(AdminAction.action)
    )
    return render_template(
        "security.html", page=page, servers=servers, actions=actions, filters=filters
    )
