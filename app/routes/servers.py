import re

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, fresh_login_required, login_required

from app.extensions import db
from app.models import (
    Alert,
    Server,
    ServerBackup,
    ServerCpuRecovery,
    ServerLog,
    ServerReportSchedule,
    ServerService,
    ServerSwapAutomation,
    ServerTerminalAccess,
    utcnow,
)
from app.services.authorization import issue_code
from app.services.encryption import encrypt
from app.services.installation import issue_installation_token, public_base_url
from app.services.installation_script import installation_script
from app.services.report_generator import latest_metric
from app.services.report_schedule import save_schedule
from app.services.security import audit
from app.services.service_inventory import restart_service
from app.services.ssh_client import SSHFailure, connect, parse_private_key
from app.services.ssh_key_manager import generate_keypair
from app.services.terminal import create_terminal_access, origin_is_allowed, terminal_socket
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
            public_base_url()
            fields = server_fields({**request.form, "ssh_username": "monitor"})
            installer = request.form.get("installation_user", "root").strip()
            if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", installer):
                raise ValueError("Некоректний користувач встановлення.")
            server = Server(
                **fields,
                ssh_private_key_encrypted="",
                installation_user=installer,
                installation_status="PENDING_SETUP",
                is_active=False,
                swap_enabled=request.form.get("enable_swap") == "on",
            )
            db.session.add(server)
            db.session.flush()
            if server.swap_enabled:
                db.session.add(ServerSwapAutomation(server_id=server.id, initial_enabled=True))
            if request.form.get("enable_database_backups") == "on":
                db.session.add(ServerBackup(server_id=server.id, enabled=True, next_at=utcnow()))
            if request.form.get("enable_cpu_recovery") == "on":
                db.session.add(ServerCpuRecovery(server_id=server.id, enabled=True))
            generate_keypair(server)
            create_terminal_access(server.id, current_user.id)
            token = issue_installation_token(server, current_user.id)
            script = installation_script(server, token)
            audit(
                current_user.id,
                "server_added",
                "Створено індивідуальний Ed25519; очікується скрипт",
                server.id,
            )
            db.session.commit()
            return render_template("server_setup.html", server=server, script=script)
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")
    return render_template("server_add.html")


@bp.get("/servers/<int:server_id>/setup")
@login_required
def setup(server_id):
    server = db.get_or_404(Server, server_id)
    if not server.ssh_public_key or server.installation_status == "ACTIVE":
        return redirect(url_for("servers.detail", server_id=server.id))
    return render_template("server_setup.html", server=server, script=None)


@bp.post("/servers/<int:server_id>/setup/script")
@login_required
def reissue_script(server_id):
    server = db.get_or_404(Server, server_id)
    if not server.ssh_public_key:
        return render_template(
            "error.html", message="Цей сервер використовує попередній спосіб SSH."
        ), 409
    try:
        token = issue_installation_token(server, current_user.id)
        script = installation_script(server, token)
        db.session.commit()
        return render_template("server_setup.html", server=server, script=script)
    except ValueError as error:
        db.session.rollback()
        flash(str(error), "error")
        return redirect(url_for("servers.setup", server_id=server.id))


@bp.get("/api/servers/<int:server_id>/setup-status")
@login_required
def setup_status(server_id):
    server = db.get_or_404(Server, server_id)
    return jsonify(
        status=server.installation_status,
        error=server.installation_error,
        expired=bool(
            server.installation_status in ("PENDING_SETUP", "INSTALLING")
            and server.installation_token_expires_at
            and server.installation_token_expires_at <= utcnow()
        ),
        authorized=server.is_authorized,
    )


@bp.route("/servers/<int:server_id>/edit", methods=["GET", "POST"])
@login_required
def edit(server_id):
    server = db.get_or_404(Server, server_id)
    if request.method == "POST":
        try:
            fields = server_fields(
                {**request.form, "ssh_username": "monitor"}
                if server.ssh_public_key
                else request.form
            )
            server = db.session.get(Server, server_id, with_for_update=True)
            thresholds = threshold_fields(request.form, server.thresholds)
            group_changed = fields["telegram_group_id"] != server.telegram_group_id
            ssh_changed = any(
                fields[field] != getattr(server, field)
                for field in ("ip_address", "ssh_port", "ssh_username")
            )
            for key, value in fields.items():
                setattr(server, key, value)
            server.thresholds = thresholds
            active = request.form.get("is_active") == "on"
            if server.ssh_public_key and active and not server.installation_completed_at:
                raise ValueError("Спочатку завершіть перевірку в майстрі підключення.")
            server.is_active = active
            if not active:
                server.installation_status = "DISABLED"
                recovery = db.session.get(ServerCpuRecovery, server.id)
                if recovery and recovery.manual_actor_id is not None and not recovery.verify_until:
                    audit(
                        recovery.manual_actor_id,
                        "server_reboot",
                        "Запит скасовано. VPS вимкнено.",
                        server.id,
                        status="cancelled",
                    )
                    recovery.manual_actor_id = None
                backup = db.session.get(ServerBackup, server.id)
                if backup and backup.requested_by is not None and backup.status != "running":
                    audit(
                        backup.requested_by,
                        "database_backup",
                        "Запит скасовано. VPS вимкнено.",
                        server.id,
                        status="cancelled",
                    )
                    backup.requested_by = None
                    backup.status = "cancelled"
            elif server.installation_status == "DISABLED":
                server.installation_status = "ACTIVE" if server.is_authorized else "READY"
            key = request.form.get("private_key", "").strip()
            if key and server.ssh_public_key:
                raise ValueError("Ключ цього сервера генерується системою й не замінюється вручну.")
            if key:
                parse_private_key(key)
                server.ssh_private_key_encrypted = encrypt(key)
            if key or ssh_changed:
                with connect(server):
                    pass
            if group_changed:
                server.is_authorized = False
                server.telegram_authorized_at = None
                if server.is_active:
                    server.installation_status = "READY"
                server.authorization_code_hash = None
                server.authorization_expires_at = None
            if "report_mode" in request.form:
                save_schedule(server, request.form)
            if "cpu_recovery_present" in request.form:
                recovery = db.session.get(ServerCpuRecovery, server.id)
                if recovery is None:
                    recovery = ServerCpuRecovery(server_id=server.id)
                    db.session.add(recovery)
                recovery.enabled = request.form.get("cpu_recovery") == "on"
                if not recovery.enabled:
                    recovery.previous_critical_at = recovery.due_at = None
                    recovery.notification_id = None
            if "automation_switches_present" in request.form:
                server.automation_enabled = request.form.get("automation_enabled") == "on"
                server.service_monitoring_enabled = (
                    request.form.get("service_monitoring_enabled") == "on"
                )
                if not server.automation_enabled:
                    recovery = db.session.get(ServerCpuRecovery, server.id)
                    if recovery:
                        recovery.previous_critical_at = recovery.due_at = (
                            recovery.notification_id
                        ) = None
            if "swap_automation_present" in request.form:
                server.swap_enabled = request.form.get("swap_permission") == "on"
                policy = db.session.get(ServerSwapAutomation, server.id)
                if policy is None:
                    policy = ServerSwapAutomation(server_id=server.id)
                    db.session.add(policy)
                initial_enabled = request.form.get("swap_initial") == "on"
                if initial_enabled and not policy.initial_enabled:
                    policy.initial_queued = False
                policy.initial_enabled = initial_enabled
                policy.critical_enabled = request.form.get("swap_critical") == "on"
            if "backup_present" in request.form:
                backup = db.session.get(ServerBackup, server.id)
                if backup is None:
                    backup = ServerBackup(server_id=server.id)
                    db.session.add(backup)
                backup.enabled = request.form.get("backup_enabled") == "on"
                if backup.enabled and backup.next_at is None:
                    backup.next_at = utcnow()
                elif not backup.enabled and backup.requested_by is None:
                    backup.next_at = None
            audit(current_user.id, "server_updated", "Налаштування оновлено", server.id)
            db.session.commit()
            flash("Налаштування збережено.")
            return redirect(url_for("servers.detail", server_id=server.id))
        except (ValueError, SSHFailure) as error:
            db.session.rollback()
            flash(str(error), "error")
    return render_template(
        "server_form.html",
        server=server,
        schedule=db.session.get(ServerReportSchedule, server.id),
        recovery=db.session.get(ServerCpuRecovery, server.id),
        swap_policy=db.session.get(ServerSwapAutomation, server.id),
        backup=db.session.get(ServerBackup, server.id),
    )


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
    services = db.session.scalars(
        db.select(ServerService)
        .where(ServerService.server_id == server.id)
        .order_by(ServerService.kind, ServerService.name)
    ).all()
    return render_template(
        "server_detail.html",
        server=server,
        metric=latest_metric(server.id),
        alerts=alerts,
        logs=logs,
        services=services,
        backup=db.session.get(ServerBackup, server.id),
        recovery=db.session.get(ServerCpuRecovery, server.id),
    )


@bp.post("/servers/<int:server_id>/reboot")
@fresh_login_required
def request_reboot(server_id):
    if request.form.get("confirmed") != "yes":
        abort(400)
    server = db.session.get(Server, server_id, with_for_update=True)
    if not server:
        abort(404)
    recovery = db.session.get(ServerCpuRecovery, server_id)
    if not server.is_active or server.status == "offline":
        flash("Перезавантаження доступне для активного VPS з підтвердженим SSH.", "error")
    elif recovery and (recovery.verify_until or recovery.manual_actor_id is not None):
        flash("Перезавантаження вже запитано.", "error")
    else:
        if recovery is None:
            recovery = ServerCpuRecovery(server_id=server_id)
            db.session.add(recovery)
        recovery.manual_actor_id = current_user.id
        server.next_check_at = utcnow()
        audit(
            current_user.id,
            "server_reboot",
            "Запит перезавантаження VPS",
            server.id,
            status="pending",
        )
        db.session.commit()
        flash("Перезавантаження додано в чергу.")
    return redirect(url_for("servers.detail", server_id=server_id))


@bp.post("/servers/<int:server_id>/backups")
@login_required
def request_backup(server_id):
    server = db.get_or_404(Server, server_id)
    db.session.execute(db.select(Server.id).where(Server.id == server.id).with_for_update())
    backup = db.session.get(ServerBackup, server.id, with_for_update=True)
    if not server.is_active:
        flash("Резервне копіювання доступне для активного VPS.", "error")
    elif backup and (backup.status == "running" or backup.requested_by is not None):
        flash("Резервне копіювання вже запитано.", "error")
    else:
        if backup is None:
            backup = ServerBackup(server_id=server.id)
            db.session.add(backup)
        backup.requested_by = current_user.id
        backup.next_at = utcnow()
        backup.status = "pending"
        audit(
            current_user.id,
            "database_backup",
            "Запит резервного копіювання",
            server.id,
            status="pending",
        )
        db.session.commit()
        flash("Резервне копіювання додано в чергу.")
    return redirect(url_for("servers.detail", server_id=server.id, _anchor="backups"))


@bp.get("/servers/<int:server_id>/terminal")
@fresh_login_required
def terminal(server_id):
    server = db.get_or_404(Server, server_id)
    access = db.session.get(ServerTerminalAccess, server_id)
    if access and access.owner_id != current_user.id:
        abort(404)
    return render_template("terminal.html", server=server, access=access)


@bp.post("/servers/<int:server_id>/terminal/setup")
@fresh_login_required
def setup_terminal(server_id):
    server = db.session.get(Server, server_id, with_for_update=True)
    if not server:
        abort(404)
    access = db.session.get(ServerTerminalAccess, server_id)
    if access and access.owner_id != current_user.id:
        abort(404)
    if not access:
        username = request.form.get("username", "").strip()
        if username in ("root", "monitor") or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", username):
            flash("Вкажіть окремий непривілейований SSH-акаунт.", "error")
        else:
            create_terminal_access(server_id, current_user.id, username)
            audit(current_user.id, "terminal_configured", "Створено ключ вебтермінала", server_id)
            db.session.commit()
    return redirect(url_for("servers.terminal", server_id=server_id))


@bp.get("/servers/<int:server_id>/terminal/socket", websocket=True)
@fresh_login_required
def terminal_ws(server_id):
    if not origin_is_allowed():
        abort(403)
    server = db.get_or_404(Server, server_id)
    access = db.session.get(ServerTerminalAccess, server_id)
    if not access or access.owner_id != current_user.id or not server.is_active:
        abort(404)
    return terminal_socket(server, access, current_user.id)


@bp.post("/servers/<int:server_id>/services/<int:service_id>/monitoring")
@login_required
def service_monitoring(server_id, service_id):
    service = db.get_or_404(ServerService, service_id)
    if service.server_id != server_id:
        abort(404)
    enabled = request.form.get("enabled")
    if enabled not in ("yes", "no"):
        abort(400)
    service.monitored = enabled == "yes"
    audit(
        current_user.id,
        "service_monitoring",
        f"{service.kind} · {service.name} · {'увімкнено' if service.monitored else 'вимкнено'}",
        server_id,
    )
    db.session.commit()
    return redirect(url_for("servers.detail", server_id=server_id, _anchor="services"))


@bp.post("/servers/<int:server_id>/services/<int:service_id>/restart")
@login_required
def service_restart(server_id, service_id):
    server = db.get_or_404(Server, server_id)
    service = db.get_or_404(ServerService, service_id)
    if service.server_id != server.id:
        abort(404)
    if request.form.get("confirmed") != "yes":
        abort(400)
    try:
        success, message = restart_service(server, service, current_user.id)
        flash(message, "message" if success else "error")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("servers.detail", server_id=server_id, _anchor="services"))


@bp.post("/servers/<int:server_id>/code")
@login_required
def regenerate_code(server_id):
    server = db.get_or_404(Server, server_id)
    try:
        code = issue_code(server)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("servers.setup", server_id=server.id))
    audit(current_user.id, "code_issued", "Код чинний 24 години", server.id)
    db.session.commit()
    return render_template(
        "authorization_code.html", server=server, code=code, wizard=bool(server.ssh_public_key)
    )


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
