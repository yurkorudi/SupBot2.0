import pytest
from cryptography.fernet import Fernet

from app import create_app
from app.extensions import db
from app.models import Server, User
from app.services.encryption import encrypt


@pytest.fixture
def app():
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret-" * 5,
            "ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "SQLALCHEMY_ENGINE_OPTIONS": {},
            "WTF_CSRF_ENABLED": False,
            "SESSION_COOKIE_SECURE": False,
            "TELEGRAM_ADMIN_ID": 123456,
        }
    )
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def server(app):
    server = Server(
        name="Production <01>",
        ip_address="8.8.8.8",
        ssh_username="monitor",
        ssh_private_key_encrypted=encrypt("PRIVATE-KEY-NEVER-RETURN"),
        telegram_group_id=-100123,
        is_authorized=True,
    )
    db.session.add(server)
    db.session.commit()
    return server


@pytest.fixture
def user(app):
    user = User(
        username="admin", totp_secret_encrypted=encrypt("JBSWY3DPEHPK3PXP"), totp_enabled=True
    )
    user.set_password("a-unique-long-test-password")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def client(app, user):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
        session["_fresh"] = True
    return client


@pytest.fixture
def sample():
    return {
        "cpu_usage": 30,
        "ram_percent": 45,
        "swap_percent": 0,
        "disk_percent": 40,
        "ram_total": 1024**3,
        "ram_used": 483183820,
        "swap_total": 0,
        "swap_used": 0,
        "disk_total": 40 * 1024**3,
        "disk_used": 16 * 1024**3,
        "details": {
            "load_average": [0.1, 0.2, 0.3],
            "uptime": 3600,
            "cpu_cores": 2,
            "processes": 42,
            "filesystems": [],
            "interfaces": [],
            "services": {},
            "errors": [],
            "network_in": 100,
            "network_out": 50,
        },
    }
