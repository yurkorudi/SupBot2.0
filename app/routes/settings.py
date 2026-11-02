from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import AdminAction, Outbox, Setting
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
    )


@bp.get("/security")
@login_required
def security():
    page = db.paginate(
        db.select(AdminAction).order_by(AdminAction.created_at.desc()), per_page=25, max_per_page=25
    )
    return render_template("security.html", page=page)
