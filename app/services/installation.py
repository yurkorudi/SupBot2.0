import hashlib
import hmac
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from flask import current_app

from app.extensions import db
from app.models import Server, utcnow

from .security import audit
from .ssh_key_manager import fingerprint, parse_host_key

TOKEN_TTL = timedelta(hours=1)
REISSUE_STATES = {
    "PENDING_SETUP",
    "INSTALLING",
    "INSTALLATION_FAILED",
    "CONNECTION_FAILED",
    "DISABLED",
}
FAILURES = {
    "unsupported_os": "Потрібен Ubuntu або Debian із systemd.",
    "dependencies": "Не вдалося встановити необхідні системні пакети.",
    "account": "Не вдалося безпечно підготувати обліковий запис monitor.",
    "files": "Не вдалося встановити файли чи публічний ключ.",
    "ssh_config": "Перевірка конфігурації SSH не пройшла.",
    "internal": "Встановлення перервано. Перевірте повідомлення у клієнтському терміналі.",
}


class InstallationError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def public_base_url():
    value = current_app.config.get("PUBLIC_BASE_URL", "").rstrip("/")
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and not parsed.path
            and not any(c.isspace() for c in value)
            and (parsed.port is None or 1 <= parsed.port <= 65535)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(
            "Задайте PUBLIC_BASE_URL=https://ваша-IP-або-домен у центральному environment та перезапустіть служби."
        )
    return value


def token_hash(token):
    return hmac.new(
        current_app.config["SECRET_KEY"].encode(),
        ("installation:" + token).encode(),
        hashlib.sha256,
    ).hexdigest()


def issue_installation_token(server, actor):
    if server.installation_status not in REISSUE_STATES:
        raise ValueError("Для цього стану повторне встановлення недоступне.")
    public_base_url()
    token = secrets.token_urlsafe(32)
    server.installation_token_hash = token_hash(token)
    server.installation_token_expires_at = utcnow() + TOKEN_TTL
    server.installation_token_used_at = None
    server.installation_status = "PENDING_SETUP"
    server.is_active = False
    server.status = "pending"
    server.installation_error = None
    server.authorization_code_hash = None
    server.authorization_expires_at = None
    audit(actor, "installation_script", "Видано скрипт на 1 годину", server.id)
    return token


def authenticated_server(server_id, token):
    server = db.session.scalar(db.select(Server).where(Server.id == server_id).with_for_update())
    if (
        not server
        or not isinstance(token, str)
        or not 20 <= len(token) <= 128
        or not server.installation_token_hash
        or not hmac.compare_digest(server.installation_token_hash, token_hash(token))
    ):
        raise InstallationError("Недійсний токен встановлення.", 403)
    if server.installation_token_used_at:
        raise InstallationError(
            "Токен уже використаний. Оновіть стан майстра або видайте новий скрипт.", 409
        )
    if not server.installation_token_expires_at or server.installation_token_expires_at <= utcnow():
        raise InstallationError("Токен прострочений. Створіть новий скрипт у панелі.", 410)
    if server.installation_status not in ("PENDING_SETUP", "INSTALLING"):
        raise InstallationError("Стан сервера не дозволяє встановлення.", 409)
    return server


def installation_started(server_id, token):
    server = authenticated_server(server_id, token)
    if server.installation_status == "PENDING_SETUP":
        server.installation_status = "INSTALLING"
        audit(f"installer:{server.id}", "installation_started", "Почато встановлення", server.id)
    db.session.commit()
    return server


def installation_completed(server_id, token, host_key):
    server = authenticated_server(server_id, token)
    _, normalized = parse_host_key(host_key)
    if server.ssh_host_key and server.ssh_host_key != normalized:
        raise InstallationError(
            "Ключ хоста змінився. Старе закріплення не замінено; потрібна перевірка адміністратора.",
            409,
        )
    server.ssh_host_key = normalized
    server.ssh_host_key_fingerprint = fingerprint(normalized)
    server.installation_token_used_at = utcnow()
    server.installation_status = "VERIFYING"
    server.installation_error = None
    server.next_check_at = utcnow()
    audit(
        f"installer:{server.id}",
        "installation_verifying",
        "HTTPS підтверджено; очікується перевірка SSH worker’ом",
        server.id,
    )
    db.session.commit()
    return server


def installation_failed(server_id, token, code):
    server = authenticated_server(server_id, token)
    server.installation_token_used_at = utcnow()
    server.installation_status = "INSTALLATION_FAILED"
    server.installation_error = FAILURES.get(
        code if isinstance(code, str) else "internal", FAILURES["internal"]
    )
    audit(f"installer:{server.id}", "installation_failed", server.installation_error, server.id)
    db.session.commit()


def expire_installations():
    rows = db.session.scalars(
        db.select(Server)
        .where(
            Server.installation_status.in_(["PENDING_SETUP", "INSTALLING"]),
            Server.installation_token_expires_at < utcnow(),
        )
        .limit(20)
        .with_for_update(skip_locked=True)
    ).all()
    for server in rows:
        server.installation_status = "INSTALLATION_FAILED"
        server.installation_error = "Токен встановлення прострочений. Видайте новий скрипт."
        audit("system", "installation_expired", server.installation_error, server.id)
    db.session.commit()
