from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.extensions import db
from app.models import ServerMetric, Setting, utcnow
from app.services.report_comments import PHRASES
from app.services.report_generator import report
from app.services.report_holidays import HOLIDAYS, holiday_for, holiday_greeting


@pytest.mark.parametrize("month_day", HOLIDAYS)
def test_fixed_holidays_have_greetings(month_day):
    month, day = month_day
    local_date, greeting = holiday_for(datetime(2026, month, day, 12, tzinfo=timezone.utc))
    assert local_date == date(2026, month, day)
    assert greeting == HOLIDAYS[month_day]


@pytest.mark.parametrize(
    "instant, expected",
    [
        (datetime(2026, 12, 31, 22, 30, tzinfo=timezone.utc), (1, 1)),
        (datetime(2026, 2, 13, 22, 30, tzinfo=timezone.utc), (2, 14)),
        (datetime(2026, 8, 23, 21, 30, tzinfo=timezone.utc), (8, 24)),
    ],
)
def test_greeting_uses_kyiv_day_in_winter_and_summer(instant, expected):
    _, greeting = holiday_for(instant)
    assert greeting == HOLIDAYS[expected]


def test_nonholiday_does_not_write_settings(server):
    assert holiday_greeting(server, datetime(2026, 11, 2, 12, tzinfo=timezone.utc)) == ""
    assert db.session.scalar(db.select(db.func.count(Setting.key))) == 0


def test_greeting_is_once_per_group_and_day_and_returns_next_year(server):
    instant = datetime(2026, 12, 25, 12, tzinfo=timezone.utc)
    assert holiday_greeting(server, instant) == HOLIDAYS[(12, 25)]
    db.session.commit()
    another = SimpleNamespace(
        id=999, telegram_group_id=server.telegram_group_id, is_authorized=True
    )
    assert holiday_greeting(another, instant) == ""
    db.session.commit()
    another.telegram_group_id = -777
    assert holiday_greeting(another, instant)
    db.session.commit()
    assert holiday_greeting(server, instant.replace(year=2027))


def test_failed_report_transaction_does_not_consume_greeting(server):
    instant = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    assert holiday_greeting(server, instant)
    db.session.rollback()
    assert holiday_greeting(server, instant)


def test_delayed_previous_day_report_cannot_reset_new_year_greeting(server):
    new_year = datetime(2026, 12, 31, 22, 30, tzinfo=timezone.utc)
    assert holiday_greeting(server, new_year) == HOLIDAYS[(1, 1)]
    db.session.commit()
    assert holiday_greeting(server, new_year.replace(hour=12)) == ""
    db.session.commit()
    assert holiday_greeting(server, new_year) == ""


def test_critical_report_keeps_advice_and_greeting(server, sample):
    db.session.add(
        ServerMetric(server_id=server.id, collected_at=utcnow(), **{**sample, "ram_percent": 95})
    )
    db.session.commit()
    with patch(
        "app.services.report_holidays.holiday_for",
        return_value=(date(2026, 2, 14), HOLIDAYS[(2, 14)]),
    ):
        text = report(server)
    assert "З Днем закоханих" in text
    assert "Перевірте споживання пам’яті процесами" in text
    assert any(phrase in text for phrase in PHRASES["critical"])


def test_negative_comments_always_have_the_requested_emotional_style():
    for level in ("warning", "critical"):
        assert all(phrase.casefold().startswith(("бля", "курва")) for phrase in PHRASES[level])
    assert not any(phrase.casefold().startswith(("бля", "курва")) for phrase in PHRASES["normal"])
    assert any("Це знову я зі звітом" in phrase for phrase in PHRASES["normal"])
