import io
import time
from contextlib import contextmanager
from pathlib import Path

import paramiko
from flask import current_app

from .encryption import decrypt


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
def connect(server, private_key=None):
    client = paramiko.SSHClient()
    try:
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
            username=server.ssh_username,
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


def execute(client, command, limit=256 * 1024, timeout=None):
    timeout = timeout or current_app.config["SSH_TIMEOUT"]
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
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
