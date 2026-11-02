from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db

from . import utcnow


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    totp_secret_encrypted = db.Column(db.Text, nullable=False)
    totp_enabled = db.Column(db.Boolean, default=False, nullable=False)
    last_totp_step = db.Column(db.BigInteger, default=-1, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    def set_password(self, password):
        if len(password) < 14:
            raise ValueError("Пароль повинен містити щонайменше 14 символів.")
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class LoginAttempt(db.Model):
    __tablename__ = "login_attempts"
    key = db.Column(db.String(64), primary_key=True)
    failures = db.Column(db.Integer, default=0, nullable=False)
    blocked_until = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime, default=utcnow, nullable=False)
