from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


from .alerts import AdminAction, Alert, Outbox  # noqa: E402, F401
from .metrics import HourlyMetric, ServerLog, ServerMetric  # noqa: E402, F401
from .server import (  # noqa: E402, F401
    Job,
    Server,
    ServerBackup,
    ServerCpuRecovery,
    ServerReportSchedule,
    ServerService,
    ServerSwapAutomation,
    ServerTerminalAccess,
    Setting,
)
from .user import LoginAttempt, User  # noqa: E402, F401
