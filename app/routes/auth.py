import time

import pyotp
from flask import Blueprint, Response, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from app.cli import qr_svg
from app.extensions import db
from app.models import User
from app.services.encryption import decrypt
from app.services.security import audit, blocked, failed, rate_key, verify_totp

bp = Blueprint("auth", __name__)
DUMMY_PASSWORD = generate_password_hash("unusable-random-comparison-value")


def pending_user():
    if time.time() - session.get("pending_at", 0) > 300:
        session.clear()
        return None
    user = (
        db.session.get(User, session.get("pending_user")) if session.get("pending_user") else None
    )
    return user if user and user.is_active else None


@bp.route("/login", methods=["GET", "POST"])
def login_view():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        username = request.form.get("username", "")[:80]
        keys = [rate_key("login-ip", request.remote_addr), rate_key("login-user", username)]
        if any(blocked(key) for key in keys):
            flash("Забагато спроб. Спробуйте через 15 хвилин.", "error")
            return render_template("login.html"), 429
        user = db.session.scalar(db.select(User).where(User.username == username))
        valid = check_password_hash(
            user.password_hash if user else DUMMY_PASSWORD, request.form.get("password", "")
        )
        if user and valid and user.is_active:
            session.clear()
            session["pending_user"] = user.id
            session["pending_at"] = time.time()
            return redirect(url_for("auth.totp"))
        for key in keys:
            failed(key)
        flash("Невірний логін або пароль.", "error")
    return render_template("login.html")


@bp.route("/totp", methods=["GET", "POST"])
def totp():
    user = pending_user()
    if not user:
        return redirect(url_for("auth.login_view"))
    key = rate_key("totp", user.id)
    if blocked(key):
        session.clear()
        return render_template("error.html", message="Забагато спроб. Зачекайте 15 хвилин."), 429
    if request.method == "POST":
        if verify_totp(user, request.form.get("code", "")):
            user.totp_enabled = True
            audit(user.id, "login", "Вхід із TOTP")
            db.session.commit()
            session.clear()
            login_user(user, remember=False, fresh=True)
            session.permanent = True
            return redirect(url_for("dashboard.index"))
        failed(key)
        flash("Невірний або вже використаний код.", "error")
    return render_template("totp.html", setup=not user.totp_enabled)


@bp.get("/totp/qr")
def totp_qr():
    user = pending_user()
    if not user or user.totp_enabled:
        return Response(status=403)
    uri = pyotp.TOTP(decrypt(user.totp_secret_encrypted)).provisioning_uri(
        name=user.username, issuer_name="ServerWatch"
    )
    return Response(qr_svg(uri), mimetype="image/svg+xml")


@bp.post("/logout")
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("auth.login_view"))
