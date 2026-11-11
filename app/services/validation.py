import ipaddress
import re


def integer(value, low, high, label):
    try:
        parsed = int(value)
        if low <= parsed <= high:
            return parsed
    except (ValueError, TypeError):
        pass
    raise ValueError(f"{label}: допустимий діапазон {low}–{high}.")


def server_fields(form):
    name = form.get("name", "").strip()
    client = form.get("client_name", "").strip()
    if not name or len(name) > 100 or len(client) > 100:
        raise ValueError("Назва обов’язкова; довжина назви та групи — до 100 символів.")
    try:
        ip = ipaddress.IPv4Address(form.get("ip_address", ""))
    except ipaddress.AddressValueError:
        raise ValueError("Вкажіть коректну публічну IPv4-адресу.") from None
    if not ip.is_global:
        raise ValueError("Потрібна публічна IPv4-адреса.")
    user = form.get("ssh_username", "")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user):
        raise ValueError("Некоректне ім’я SSH-користувача.")
    group = form.get("telegram_group_id", "").strip()
    group_id = integer(group, -(2**63) + 1, -1, "Telegram Group ID") if group else None
    services = [s.strip() for s in form.get("services", "").split(",") if s.strip()]
    if len(services) > 20 or any(
        not re.fullmatch(r"[A-Za-z0-9_.@-]{1,90}\.service", s) for s in services
    ):
        raise ValueError("До 20 назв сервісів .service через кому.")
    return {
        "name": name,
        "ip_address": str(ip),
        "ssh_username": user,
        "client_name": client,
        "ssh_port": integer(form.get("ssh_port", 22), 1, 65535, "SSH-порт"),
        "monitoring_interval": integer(form.get("monitoring_interval", 180), 60, 86400, "Інтервал"),
        "telegram_group_id": group_id,
        "services": list(dict.fromkeys(services)),
    }


def threshold_fields(form, defaults):
    defaults = {
        **defaults,
        "cpu_critical": defaults.get("cpu_critical", max(95, defaults["cpu_warning"])),
    }
    result = {key: integer(form.get(key, value), 1, 100, key) for key, value in defaults.items()}
    if result["cpu_warning"] > result["cpu_critical"]:
        raise ValueError("Критичний поріг CPU має бути не нижчим за поріг попередження.")
    for key in ("ram", "disk"):
        if result[key + "_warning"] >= result[key + "_critical"]:
            raise ValueError("Критичний поріг повинен перевищувати попереджувальний.")
    return result
