"""Holiday greetings use the Ukrainian calendar, once per group and local day."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import update

from app.extensions import db
from app.models import Setting

from .report_comments import upsert_setting

HOLIDAYS = {
    (1, 1): (
        "🎆 З Новим роком! Бажаю вам спокою, гарних новин і жодного "
        "«терміново, продакшен» о третій ночі."
    ),
    (2, 14): (
        "💘 З Днем закоханих! Нехай вас люблять не тільки боти, "
        "а обійми будуть частішими за алерти."
    ),
    (3, 8): (
        "🌷 З 8 березня! Поваги, рівних можливостей і вечора без робочого «а можеш швиденько?»."
    ),
    (4, 1): (
        "😄 З Днем сміху! Жарти сьогодні дозволені, але цифри у звіті справжні — "
        "тут без розіграшів."
    ),
    (6, 28): (
        "🇺🇦 З Днем Конституції України! Свободи вам, "
        "а адмінських прав — тільки тим, кому справді треба."
    ),
    (8, 24): (
        "🇺🇦 З Днем Незалежності України! Мирного неба, свободи та людей поруч, "
        "заради яких усе це має сенс."
    ),
    (10, 1): (
        "🇺🇦 З Днем захисників і захисниць України. "
        "Дякуємо тим, завдяки кому можемо жити й працювати."
    ),
    (12, 6): (
        "🎁 З Днем святого Миколая! Хай подарунки будуть приємними, "
        "а не у вигляді нової задачі на вечір."
    ),
    (12, 25): (
        "🎄 З Різдвом! Тепла, своїх людей поруч і тихого вечора без дзвінка «ти біля комп’ютера?»."
    ),
    (12, 31): (
        "🥂 З прийдешнім Новим роком! Бажаю встигнути загадати бажання, "
        "а не виправляти конфіг під куранти."
    ),
}


def holiday_for(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(ZoneInfo("Europe/Kyiv"))
    return local.date(), HOLIDAYS.get((local.month, local.day), "")


def holiday_greeting(server, now=None):
    day, greeting = holiday_for(now)
    if not greeting:
        return ""
    if server.is_authorized and server.telegram_group_id:
        audience = f"group:{server.telegram_group_id}"
    else:
        audience = f"server:{server.id}"
    key = f"report_holiday:{audience}"
    stamp = int(day.strftime("%Y%m%d"))
    # The upsert locks this row until the report/outbox transaction commits.
    upsert_setting(key, 0, Setting.value)
    previous = db.session.scalar(
        db.select(Setting.value).where(Setting.key == key).with_for_update()
    )
    if previous >= stamp:
        return ""
    db.session.execute(update(Setting).where(Setting.key == key).values(value=stamp))
    return greeting
