import asyncio
from datetime import timedelta

import pytest

from app.extensions import db
from app.models import ServerMetric, Setting, utcnow
from app.services.report_comments import PHRASES, load_level, rotating_comment
from app.services.report_generator import report
from bot.services.backend import Backend


def metric_for(server, sample, **changes):
    return ServerMetric(server_id=server.id, collected_at=utcnow(), **{**sample, **changes})


def test_each_load_level_has_twenty_distinct_comments():
    for phrases in PHRASES.values():
        assert len(phrases) == len(set(phrases)) == 20


@pytest.mark.parametrize("level", ["normal", "warning", "critical"])
def test_full_rotation_survives_session_changes_without_repeats(server, level):
    comments = []
    for _ in range(21):
        comments.append(rotating_comment(server, level))
        db.session.commit()
        db.session.expire_all()
    assert len(set(comments[:20])) == 20
    assert comments[20] == comments[0]
    assert comments[19] != comments[20]


def test_load_levels_follow_server_thresholds_and_data_freshness(server, sample):
    metric = metric_for(server, sample)
    assert load_level(server, metric) == "normal"
    metric.ram_percent = 85
    assert load_level(server, metric) == "warning"
    metric.ram_percent = 95
    assert load_level(server, metric) == "critical"
    metric.ram_percent = 45
    server.thresholds = {**server.thresholds, "ram_warning": 30, "ram_critical": 40}
    assert load_level(server, metric) == "critical"
    metric.collected_at = utcnow() - timedelta(minutes=7)
    assert load_level(server, metric) == "unknown"
    metric.collected_at = utcnow()
    server.status = "offline"
    assert load_level(server, metric) == "unknown"


def test_inactive_service_triggers_warning_comment(server, sample):
    metric = metric_for(
        server, sample, details={**sample["details"], "services": {"nginx.service": "failed"}}
    )
    assert load_level(server, metric) == "warning"


def test_rotation_has_separate_cursor_for_each_load_level(server):
    normal = rotating_comment(server, "normal")
    db.session.commit()
    rotating_comment(server, "critical")
    db.session.commit()
    assert rotating_comment(server, "normal") != normal
    db.session.commit()
    assert db.session.scalar(db.select(db.func.count(Setting.key))) == 2


def test_overload_has_real_advice_and_a_critical_comment(server, sample):
    db.session.add(metric_for(server, sample, ram_percent=95, disk_percent=97))
    db.session.commit()
    text = report(server)
    assert "Перевірте споживання пам’яті процесами" in text
    assert "ротацію логів" in text
    assert any(phrase in text for phrase in PHRASES["critical"])
    assert "Можна випити кави" not in text
    assert "збільшувати SWAP немає необхідності" not in text


def test_stale_report_does_not_claim_health_or_advance_rotation(server, sample):
    metric = metric_for(server, sample)
    metric.collected_at = utcnow() - timedelta(hours=1)
    db.session.add(metric)
    db.session.commit()
    text = report(server)
    assert "Відновіть перевірку сервера" in text
    assert not any(phrase in text for phrases in PHRASES.values() for phrase in phrases)
    assert (
        db.session.scalar(
            db.select(db.func.count(Setting.key)).where(Setting.key.like("report_comment:%"))
        )
        == 0
    )


def test_bot_commits_comment_cursor_and_start_check_does_not_consume_it(app, server, sample):
    db.session.add(metric_for(server, sample))
    db.session.commit()
    first = asyncio.run(Backend(app).reports(server.telegram_group_id))[0]
    key = f"report_comment:{server.id}:normal"
    before = db.session.scalar(db.select(Setting.value).where(Setting.key == key))
    assert before is not None
    assert asyncio.run(Backend(app).is_bound(server.telegram_group_id))
    assert db.session.scalar(db.select(Setting.value).where(Setting.key == key)) == before
    second = asyncio.run(Backend(app).reports(server.telegram_group_id))[0]
    assert first != second
    after = db.session.scalar(db.select(Setting.value).where(Setting.key == key))
    assert after == (before + 1) % 20
