import json
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from flask import current_app


def comment(metric):
    if not current_app.config["AI_ENABLED"]:
        return ""
    url = current_app.config["AI_URL"]
    # Only local Ollama is supported: no billing or sensitive outbound context.
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        return ""
    values = {
        key: getattr(metric, key)
        for key in ("cpu_usage", "ram_percent", "swap_percent", "disk_percent")
    }
    payload = {
        "model": current_app.config["AI_MODEL"],
        "stream": False,
        "prompt": "Українською, до 2 речень: поясни відсотки навантаження без команд. "
        "Можна легкий жарт. Дані: " + json.dumps(values),
        "options": {"num_predict": 100},
    }
    try:
        req = Request(
            url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
        )
        with urlopen(req, timeout=4) as response:
            return str(json.loads(response.read(8192)).get("response", ""))[:400]
    except (OSError, ValueError, TypeError):
        return ""
