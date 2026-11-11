import json
import threading
import time
from contextlib import suppress
from urllib.parse import urlsplit

from cryptography.fernet import InvalidToken
from flask import Response, current_app, request
from simple_websocket import ConnectionClosed
from simple_websocket import Server as WebSocket

from app.extensions import db
from app.models import Server, ServerTerminalAccess, User

from .encryption import decrypt
from .security import audit
from .ssh_client import SSHFailure, connect
from .ssh_key_manager import new_keypair

TERMINAL_USER = "serverwatch-terminal"
IDLE_SECONDS = 300
MAX_SESSION_SECONDS = 1800
# The deployed Gunicorn service runs one worker. Keep web threads available for other requests.
SESSION_SLOT = threading.BoundedSemaphore(1)


def create_terminal_access(server_id, owner_id, username=TERMINAL_USER):
    encrypted, public = new_keypair(f"serverwatch-terminal-{server_id}")
    access = ServerTerminalAccess(
        server_id=server_id,
        owner_id=owner_id,
        username=username,
        ssh_private_key_encrypted=encrypted,
        ssh_public_key=public,
    )
    db.session.add(access)
    return access


def allowed_origin(origin, public_url, request_host, secure):
    if not origin or origin == "null":
        return False
    try:
        expected = urlsplit(public_url) if public_url else None
        candidate = urlsplit(origin)
        if (
            candidate.path
            or candidate.query
            or candidate.fragment
            or candidate.username is not None
        ):
            return False
        if expected and expected.scheme == candidate.scheme == "https":
            if candidate.netloc == expected.netloc == request_host:
                return True
        return (
            not secure
            and candidate.scheme == "http"
            and candidate.netloc == request_host
            and candidate.hostname in ("127.0.0.1", "localhost", "::1")
        )
    except ValueError:
        return False


def access_still_valid(server_id, owner_id, public_key):
    result = db.session.scalar(
        db.select(ServerTerminalAccess.server_id)
        .join(Server, Server.id == ServerTerminalAccess.server_id)
        .join(User, User.id == ServerTerminalAccess.owner_id)
        .where(
            ServerTerminalAccess.server_id == server_id,
            ServerTerminalAccess.owner_id == owner_id,
            ServerTerminalAccess.ssh_public_key == public_key,
            User.is_active.is_(True),
            Server.is_active.is_(True),
        )
    )
    db.session.remove()
    return result is not None


def bridge(ws, channel, check_access):
    start = last_activity = time.monotonic()
    next_check = start + 15
    while True:
        now = time.monotonic()
        if now - start >= MAX_SESSION_SECONDS or now - last_activity >= IDLE_SECONDS:
            return "Ліміт тривалості або неактивності."
        if now >= next_check:
            if not check_access():
                return "Доступ відкликано."
            next_check = now + 15
        if channel.recv_ready():
            ws.send(channel.recv(8192))
            last_activity = now
        if channel.recv_stderr_ready():
            ws.send(channel.recv_stderr(8192))
            last_activity = now
        if (
            channel.exit_status_ready()
            and not channel.recv_ready()
            and not channel.recv_stderr_ready()
        ):
            return "Віддалена сесія завершена."
        message = ws.receive(timeout=0.03)
        if isinstance(message, bytes):
            channel.sendall(message)
            last_activity = now
        elif isinstance(message, str):
            try:
                size = json.loads(message)
                if isinstance(size, dict) and size.get("close") is True:
                    return "Від’єднано"
                columns, rows = size["cols"], size["rows"]
                if (
                    type(columns) is int
                    and type(rows) is int
                    and 2 <= columns <= 400
                    and 2 <= rows <= 160
                ):
                    channel.resize_pty(width=columns, height=rows)
            except (ValueError, KeyError, TypeError):
                continue


class UpgradedResponse(Response):
    def __init__(self, mode):
        super().__init__()
        self.mode = mode

    def __call__(self, environ, start_response):
        if self.mode == "gunicorn":
            raise StopIteration
        return super().__call__(environ, start_response)


def terminal_socket(server, access, actor):
    server_id = server.id
    username, key, public_key = (
        access.username,
        access.ssh_private_key_encrypted,
        access.ssh_public_key,
    )
    if not SESSION_SLOT.acquire(blocking=False):
        return Response(status=429)
    try:
        db.session.expunge(server)
        db.session.remove()
        ws = WebSocket.accept(request.environ, ping_interval=25, max_message_size=16384)
        result = "Браузер закрив з’єднання."
        status = "done"
        try:
            with connect(server, decrypt(key), username=username) as client:
                timeout = current_app.config["SSH_TIMEOUT"]
                channel = client.get_transport().open_session(timeout=timeout)
                handshake = threading.Timer(timeout, channel.close)
                handshake.daemon = True
                handshake.start()
                try:
                    channel.get_pty(term="xterm-256color", width=80, height=24)
                    channel.invoke_shell()
                    channel.settimeout(5)
                finally:
                    handshake.cancel()
                audit(actor, "terminal_opened", "SSH PTY відкрито", server_id)
                db.session.commit()
                db.session.remove()
                ws.send(json.dumps({"state": "connected"}))
                result = bridge(
                    ws, channel, lambda: access_still_valid(server_id, actor, public_key)
                )
        except ConnectionClosed:
            pass
        except (SSHFailure, InvalidToken, ValueError):
            result, status = "Не вдалося підключитися або SSH-сесію перервано.", "failed"
        finally:
            audit(actor, "terminal_closed", result, server_id, status=status)
            db.session.commit()
            with suppress(ConnectionClosed, OSError):
                ws.send(json.dumps({"state": "closed", "message": result}))
                ws.close()
        return UpgradedResponse(ws.mode)
    finally:
        SESSION_SLOT.release()


def origin_is_allowed():
    return allowed_origin(
        request.headers.get("Origin"),
        current_app.config["PUBLIC_BASE_URL"],
        request.host,
        current_app.config["SESSION_COOKIE_SECURE"],
    )
