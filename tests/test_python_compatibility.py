import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_application_syntax_is_compatible_with_python310():
    for folder in ("app", "bot", "monitoring", "scripts", "migrations"):
        for path in (ROOT / folder).rglob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 10))


def test_models_and_monitoring_do_not_require_datetime_utc_alias():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import datetime
if hasattr(datetime, 'UTC'):
    del datetime.UTC
from app.models import utcnow
import monitoring.collectors
import monitoring.worker
assert utcnow().tzinfo is None
assert abs((utcnow() - datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)).total_seconds()) < 2
""",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
