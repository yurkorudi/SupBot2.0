from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Alert, Server, ServerLog
from app.services.authorization import issue_code
from app.services.encryption import encrypt
from app.services.report_generator import latest_metric
from app.services.security import audit
from app.services.ssh_client import SSHFailure, connect, parse_private_key
from app.services.validation import server_fields, threshold_fields

bp = Blueprint("servers", __name__)


@bp.get("/servers")
@login_required
def index():
    page = db.paginate(db.select(Server).order_by(Server.id.desc()), per_page=20, max_per_page=20)
    return render_template("servers.html", page=page)


@bp.route("/servers/add", methods=["GET", "POST"])
@login_required
def add():
    if request.method == "POST":
        try:
            fields = server_fields(request.form)
            key = request.form.get("private_key", "").strip()
            parse_private_key(key)
            server = Server(**fields, ssh_private_key_encrypted=encrypt(key))
            with connect(server, private_key=key):
                pass
            db.session.add(server)
            db.session.flush()
            code = issue_code(server)
            audit(current_user.id, "server_added", "SSH перевірено; ключ зашифровано", server.id)
            db.session.commit()
            return render_template("authorization_code.html", server=server, code=code)
        except (ValueError, SSHFailure) as error:
            db.session.rollback()
            flash(str(error), "error")
    return render_template("server_form.html", server=None)


@bp.route("/servers/<int:server_id>/edit", methods=["GET", "POST"])
@login_required
def edit(server_id):
    server = db.get_or_404(Server, server_id)
    if request.method == "POST":
        try:
            fields = server_fields(request.form)
            thresholds = threshold_fields(request.form, server.thresholds)
            group_changed = fields["telegram_group_id"] != server.telegram_group_id
            ssh_changed = any(
                fields[field] != getattr(server, field)
                for field in ("ip_address", "ssh_port", "ssh_username")
            )
            for key, value in fields.items():
                setattr(server, key, value)
            server.thresholds = thresholds
            server.is_active = request.form.get("is_active") == "on"
            key = request.form.get("private_key", "").strip()
            if key:
                parse_private_key(key)
                server.ssh_private_key_encrypted = encrypt(key)
            if key or ssh_changed:
                with connect(server):
                    pass
            if group_changed:
                server.is_authorized = False
                server.authorization_code_hash = None
                server.authorization_expires_at = None
            audit(current_user.id, "server_updated", "Налаштування оновлено", server.id)
            db.session.commit()
            flash("Налаштування збережено.")
            return redirect(url_for("servers.detail", server_id=server.id))
        except (ValueError, SSHFailure) as error:
            db.session.rollback()
            flash(str(error), "error")
    return render_template("server_form.html", server=server)


@bp.get("/servers/<int:server_id>")
@login_required
def detail(server_id):
    server = db.get_or_404(Server, server_id)
    alerts = db.paginate(
        db.select(Alert).where(Alert.server_id == server.id).order_by(Alert.created_at.desc()),
        per_page=10,
        max_per_page=10,
    )
    logs = db.session.scalars(
        db.select(ServerLog)
        .where(ServerLog.server_id == server.id)
        .order_by(ServerLog.created_at.desc())
        .limit(20)
    ).all()
    return render_template(
        "server_detail.html",
        server=server,
        metric=latest_metric(server.id),
        alerts=alerts,
        logs=logs,
    )


@bp.post("/servers/<int:server_id>/code")
@login_required
def regenerate_code(server_id):
    server = db.get_or_404(Server, server_id)
    code = issue_code(server)
    audit(current_user.id, "code_issued", "Код чинний 24 години", server.id)
    db.session.commit()
    return render_template("authorization_code.html", server=server, code=code)


@bp.get("/api/servers/<int:server_id>")
@login_required
def server_api(server_id):
    return jsonify(db.get_or_404(Server, server_id).public_dict())


@bp.get("/servers/<int:server_id>/logs")
@login_required
def logs(server_id):
    server = db.get_or_404(Server, server_id)
    page = db.paginate(
        db.select(ServerLog)
        .where(ServerLog.server_id == server.id)
        .order_by(ServerLog.created_at.desc()),
        per_page=25,
        max_per_page=25,
    )
    return render_template("logs.html", server=server, page=page)
