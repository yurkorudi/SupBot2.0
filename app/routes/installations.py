from flask import Blueprint, jsonify, request

from app.extensions import csrf, db
from app.services.installation import (
    InstallationError,
    installation_completed,
    installation_failed,
    installation_started,
)
from app.services.security import audit, blocked, failed, rate_key

bp = Blueprint("installations", __name__)
csrf.exempt(bp)


@bp.post("/api/installations/<int:server_id>/<action>")
def callback(server_id, action):
    if not request.is_secure:
        return jsonify(message="Потрібне HTTPS-з’єднання."), 400
    key = rate_key("installation-ip", request.remote_addr)
    if blocked(key):
        return jsonify(message="Забагато невдалих спроб. Зачекайте 15 хвилин."), 429
    if action not in ("started", "completed", "failed"):
        return jsonify(message="Невідома операція."), 404
    authorization = request.headers.get("Authorization", "")
    token = authorization[7:] if authorization.startswith("Bearer ") else ""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(message="Потрібен JSON-об’єкт."), 400
    try:
        if action == "started":
            installation_started(server_id, token)
            return jsonify(status="INSTALLING")
        if action == "completed":
            installation_completed(server_id, token, data.get("host_key"))
            return jsonify(status="VERIFYING"), 202
        installation_failed(server_id, token, data.get("error_code", "internal"))
        return jsonify(status="INSTALLATION_FAILED")
    except (InstallationError, ValueError) as error:
        db.session.rollback()
        failed(key)
        audit(
            f"install:{request.remote_addr}", "installation_denied", "Запит встановлення відхилено"
        )
        db.session.commit()
        return jsonify(message=str(error)), getattr(error, "status", 400)
