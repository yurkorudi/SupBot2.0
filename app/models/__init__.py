from datetime import UTC, datetime


def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


from .alerts import AdminAction, Alert, Outbox  # noqa: E402, F401
from .metrics import HourlyMetric, ServerLog, ServerMetric  # noqa: E402, F401
from .server import Job, Server, Setting  # noqa: E402, F401
from .user import LoginAttempt, User  # noqa: E402, F401
