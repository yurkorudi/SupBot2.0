import os
from datetime import timedelta

from dotenv import load_dotenv


def configuration():
    load_dotenv()
    return {
        "SECRET_KEY": os.getenv("SECRET_KEY", ""),
        "PUBLIC_BASE_URL": os.getenv("PUBLIC_BASE_URL", ""),
        "ENCRYPTION_KEY": os.getenv("ENCRYPTION_KEY", ""),
        "SQLALCHEMY_DATABASE_URI": os.getenv("DATABASE_URL", ""),
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "SQLALCHEMY_ENGINE_OPTIONS": {"pool_pre_ping": True, "pool_recycle": 1800},
        "SESSION_COOKIE_SECURE": os.getenv("SESSION_COOKIE_SECURE", "true").lower() == "true",
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "PERMANENT_SESSION_LIFETIME": timedelta(hours=8),
        "MAX_CONTENT_LENGTH": 64 * 1024,
        "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "TELEGRAM_ADMIN_ID": int(os.getenv("TELEGRAM_ADMIN_ID") or 0),
        "SSH_KNOWN_HOSTS": os.getenv("SSH_KNOWN_HOSTS", "/etc/serverwatch/known_hosts"),
        "SSH_TIMEOUT": int(os.getenv("SSH_TIMEOUT", "12")),
        "MONITOR_CONCURRENCY": max(1, min(4, int(os.getenv("MONITOR_CONCURRENCY", "2")))),
        "AI_ENABLED": os.getenv("AI_ENABLED", "false").lower() == "true",
        "AI_URL": os.getenv("AI_URL", "http://127.0.0.1:11434/api/generate"),
        "AI_MODEL": os.getenv("AI_MODEL", "llama3.2:1b"),
    }
