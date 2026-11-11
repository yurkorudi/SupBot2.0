import io
import time
from contextlib import contextmanager
from pathlib import Path

import paramiko
from flask import current_app

from .encryption import decrypt
from .ssh_key_manager import parse_host_key


class SSHFailure(Exception):
    pass


def parse_private_key(value):
    for kind in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return kind.from_private_key(io.StringIO(value))
        except paramiko.PasswordRequiredException:
            raise ValueError(
                "Ключ захищений парольною фразою. Для фонового моніторингу потрібен "
                "окремий ключ без парольної фрази; перед записом у БД він шифрується."
            ) from None
        except (paramiko.SSHException, ValueError):
            continue
    raise ValueError(
        "Некоректний ключ. Використайте незашифрований Ed25519, ECDSA або RSA PEM/OpenSSH."
    )


@contextmanager
def connect(server, private_key=None, *, username=None):
    client = paramiko.SSHClient()
    try:
        if server.ssh_host_key:
            host_key, _ = parse_host_key(server.ssh_host_key)
            hostname = (
                server.ip_address
                if server.ssh_port == 22
                else f"[{server.ip_address}]:{server.ssh_port}"
            )
            client.get_host_keys().add(hostname, host_key.get_name(), host_key)
        else:
            known_hosts = current_app.config["SSH_KNOWN_HOSTS"]
            if not Path(known_hosts).is_file():
                raise SSHFailure("Відсутній перевірений файл known_hosts.")
            client.load_host_keys(known_hosts)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        timeout = current_app.config["SSH_TIMEOUT"]
        key = parse_private_key(private_key or decrypt(server.ssh_private_key_encrypted))
        client.connect(
            server.ip_address,
            port=server.ssh_port,
            username=username or server.ssh_username,
            pkey=key,
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
            allow_agent=False,
            look_for_keys=False,
        )
        yield client
    except SSHFailure:
        raise
    except TimeoutError:
        raise SSHFailure("Час очікування SSH вичерпано.") from None
    except paramiko.BadHostKeyException:
        raise SSHFailure("Ключ хоста SSH змінився.") from None
    except paramiko.AuthenticationException:
        raise SSHFailure("SSH відхилив автентифікацію.") from None
    except (paramiko.SSHException, OSError, ValueError):
        raise SSHFailure("SSH-з’єднання не встановлено або ключ хоста не підтверджено.") from None
    finally:
        client.close()


def execute(client, command, limit=256 * 1024, timeout=None, input_data=None):
    timeout = timeout or current_app.config["SSH_TIMEOUT"]
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    if input_data is not None:
        stdin.write(input_data)
        stdin.flush()
    stdin.close()
    channel = stdout.channel
    deadline = time.monotonic() + timeout
    data = bytearray()
    while True:
        if time.monotonic() > deadline:
            channel.close()
            raise SSHFailure("Час виконання SSH-команди вичерпано.")
        if channel.recv_ready():
            data.extend(channel.recv(min(8192, limit + 1 - len(data))))
            if len(data) > limit:
                channel.close()
                raise SSHFailure("Відповідь SSH перевищила обмеження.")
        if channel.recv_stderr_ready():
            channel.recv_stderr(8192)
        if channel.exit_status_ready() and not channel.recv_ready():
            break
        time.sleep(0.02)
    if channel.recv_exit_status() != 0:
        raise SSHFailure("Віддалена операція завершилася помилкою.")
    return data.decode("utf-8", errors="replace")


def stream_to(client, command, target, *, timeout, limit):
    stdin, stdout, _ = client.exec_command(command, timeout=timeout)
    stdin.close()
    channel = stdout.channel
    deadline = time.monotonic() + timeout
    size = 0
    try:
        while time.monotonic() < deadline:
            if channel.recv_stderr_ready():
                channel.recv_stderr(32768)
            if channel.recv_ready():
                block = channel.recv(65536)
                size += len(block)
                if size > limit:
                    raise SSHFailure("Розмір дампу перевищив ліміт.")
                target.write(block)
            elif channel.exit_status_ready():
                if size == 0 or channel.recv_exit_status() != 0:
                    raise SSHFailure("Віддалений дамп порожній або команда завершилася помилкою.")
                return size
            else:
                time.sleep(0.02)
        raise SSHFailure("Час створення дампу вичерпано.")
    finally:
        channel.close()
