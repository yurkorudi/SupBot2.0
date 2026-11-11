import hashlib
import time
from datetime import timedelta

import pyotp
from flask import current_app
from sqlalchemy import update

from app.extensions import db
from app.models import AdminAction, LoginAttempt, User, utcnow

from .encryption import decrypt


def audit(actor, action, result, server_id=None, *, source="manual", status="done"):
    db.session.add(
        AdminAction(
            admin_id=str(actor),
            action=action,
            result=result,
            server_id=server_id,
            source=source,
            status=status,
        )
    )


def require_admin(user_id):
    configured = current_app.config["TELEGRAM_ADMIN_ID"]
    if not configured or user_id != configured:
        audit(
            user_id, "telegram_denied", "Відмовлено у доступі", source="telegram", status="failed"
        )
        db.session.commit()
        raise PermissionError("Дія доступна лише головному адміністратору.")


def rate_key(namespace, identity):
    return hashlib.sha256(f"{namespace}:{identity}".encode()).hexdigest()


def blocked(key):
    item = db.session.get(LoginAttempt, key)
    return bool(item and item.blocked_until and item.blocked_until > utcnow())


def failed(key):
    item = db.session.get(LoginAttempt, key)
    now = utcnow()
    if not item:
        item = LoginAttempt(key=key, failures=0)
        db.session.add(item)
    if item.updated_at and now - item.updated_at > timedelta(minutes=15):
        item.failures = 0
    item.failures += 1
    item.updated_at = now
    if item.failures >= 5:
        item.blocked_until = now + timedelta(minutes=15)
    db.session.commit()


def verify_totp(user, code, at=None):
    if len(code) != 6 or not code.isascii() or not code.isdigit():
        return False
    stamp = time.time() if at is None else at
    totp = pyotp.TOTP(decrypt(user.totp_secret_encrypted))
    for step in (int(stamp // 30), int(stamp // 30) - 1, int(stamp // 30) + 1):
        if totp.at(step * 30) == code:
            result = db.session.execute(
                update(User)
                .where(User.id == user.id, User.last_totp_step < step)
                .values(last_totp_step=step)
            )
            return result.rowcount == 1
    return False
