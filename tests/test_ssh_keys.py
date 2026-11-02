import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from app.services.ssh_client import parse_private_key


@pytest.mark.parametrize("kind", ["ed25519", "ecdsa", "rsa"])
def test_accepts_unencrypted_openssh_keys(kind):
    if kind == "ed25519":
        key = ed25519.Ed25519PrivateKey.generate()
    elif kind == "ecdsa":
        key = ec.generate_private_key(ec.SECP256R1())
    else:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    value = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    ).decode()
    assert parse_private_key(value).get_name() in ("ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa")


def test_encrypted_key_has_specific_message_without_secret():
    key = ed25519.Ed25519PrivateKey.generate()
    value = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.BestAvailableEncryption(b"''"),
    ).decode()
    with pytest.raises(ValueError, match="парольною фразою") as error:
        parse_private_key(value)
    assert value not in str(error.value)
