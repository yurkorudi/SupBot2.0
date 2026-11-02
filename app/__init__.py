import logging

from cryptography.fernet import Fernet
from flask import Flask, render_template
from flask_wtf.csrf import CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import configuration
from .extensions import csrf, db, login, migrate


def create_app(test_config=None):
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    app.config.update(configuration())
    if test_config:
        app.config.update(test_config)
    if len(app.config["SECRET_KEY"]) < 32:
        raise RuntimeError("Задайте SECRET_KEY довжиною щонайменше 32 символи.")
    Fernet(app.config["ENCRYPTION_KEY"].encode())
    if not app.config["SQLALCHEMY_DATABASE_URI"]:
        raise RuntimeError("Задайте DATABASE_URL.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("paramiko").setLevel(logging.CRITICAL)
    db.init_app(app)
    login.init_app(app)
    csrf.init_app(app)
    migrate.init_app(app, db)
    login.login_view = "auth.login_view"
    login.login_message = "Увійдіть, щоб продовжити."
    from .cli import register_commands
    from .models import User
    from .routes import auth, dashboard, servers, settings

    @login.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    for blueprint in (auth.bp, dashboard.bp, servers.bp, settings.bp):
        app.register_blueprint(blueprint)
    register_commands(app)

    @app.after_request
    def secure_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        if app.config["SESSION_COOKIE_SECURE"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        return render_template("error.html", message="Сеанс форми минув. Оновіть сторінку."), 400

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", message="Сторінку не знайдено."), 404

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        return render_template(
            "error.html", message="Внутрішня помилка. Перевірте стан служб."
        ), 500

    return app
