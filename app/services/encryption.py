from cryptography.fernet import Fernet
from flask import current_app


def encrypt(value):
    return Fernet(current_app.config["ENCRYPTION_KEY"].encode()).encrypt(value.encode()).decode()


def decrypt(value):
    return Fernet(current_app.config["ENCRYPTION_KEY"].encode()).decrypt(value.encode()).decode()
