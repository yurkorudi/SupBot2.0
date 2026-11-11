import base64
import hashlib

import paramiko
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .encryption import encrypt


def fingerprint(public_key):
    blob = base64.b64decode(public_key.split()[1], validate=True)
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def new_keypair(comment):
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        .decode()
    )
    return encrypt(private), f"{public} {comment}"


def generate_keypair(server):
    server.ssh_private_key_encrypted, server.ssh_public_key = new_keypair(
        f"serverwatch-server-{server.id}"
    )
    public = server.ssh_public_key
    server.ssh_key_fingerprint = fingerprint(public)


def parse_host_key(value):
    try:
        if not isinstance(value, str) or len(value) > 8192:
            raise ValueError
        fields = value.strip().split()
        if len(fields) < 2 or fields[0] not in (
            "ssh-ed25519",
            "ssh-rsa",
            "ecdsa-sha2-nistp256",
            "ecdsa-sha2-nistp384",
            "ecdsa-sha2-nistp521",
        ):
            raise ValueError
        key = paramiko.PKey.from_type_string(fields[0], base64.b64decode(fields[1], validate=True))
        return key, f"{key.get_name()} {key.get_base64()}"
    except (ValueError, TypeError, IndexError, paramiko.SSHException):
        raise ValueError("Некоректний публічний ключ SSH-хоста.") from None
