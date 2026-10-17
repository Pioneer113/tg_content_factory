"""Интеграционные тесты PingMonitorRepository против реального SQLite (#1415)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from tg_agent.models import PingMonitor


@pytest.fixture
def repo(db):
    return db.repos.ping_monitors


def _monitor(**overrides) -> PingMonitor:
    defaults = dict(
        phone="+1234",
        dialog_id=777,
        dialog_title="Иван",
        silence_threshold_days=14,
        nudge_text="привет, как ты?",
    )
    defaults.update(overrides)
    return PingMonitor(**defaults)


async def test_create_get_roundtrip(db, repo):
    monitor_id = await repo.create(_monitor())

    loaded = await repo.get(monitor_id)

    assert loaded is not None
    assert loaded.phone == "+1234"
    assert loaded.dialog_id == 777
    assert loaded.dialog_title == "Иван"
    assert loaded.silence_threshold_days == 14
    assert loaded.nudge_text == "привет, как ты?"
    assert loaded.is_active is True
    assert loaded.cycle_count == 0
    assert loaded.created_at is not None


async def test_unique_phone_dialog_pair(db, repo):
    await repo.create(_monitor())
    with pytest.raises(sqlite3.IntegrityError):
        await repo.create(_monitor())


async def test_list_orders_by_id_and_filters_active(db, repo):
    first = await repo.create(_monitor())
    second = await repo.create(_monitor(phone="+5678", dialog_id=888))
    await repo.update(second, is_active=False)

    all_monitors = await repo.list()
    assert [m.id for m in all_monitors] == [first, second]

    active = await repo.list(active_only=True)
    assert [m.id for m in active] == [first]


async def test_update_partial_fields(db, repo):
    monitor_id = await repo.create(_monitor())
    when = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)

    await repo.update(
        monitor_id,
        nudge_text="ну что, живой?",
        silence_threshold_days=7,
        cycle_count=2,
        last_checked_at=when,
        last_nudge_sent_at=when,
    )
    loaded = await repo.get(monitor_id)

    assert loaded.nudge_text == "ну что, живой?"
    assert loaded.silence_threshold_days == 7
    assert loaded.cycle_count == 2
    assert loaded.last_checked_at == when
    assert loaded.last_nudge_sent_at == when
    # Нетронутые поля не изменились.
    assert loaded.dialog_title == "Иван"


async def test_update_empty_is_noop(db, repo):
    monitor_id = await repo.create(_monitor())

    await repo.update(monitor_id)

    assert await repo.get(monitor_id) is not None


async def test_update_error_field(db, repo):
    monitor_id = await repo.create(_monitor())

    await repo.update(monitor_id, error="flood wait")
    assert (await repo.get(monitor_id)).error == "flood wait"

    await repo.update(monitor_id, error="")
    assert (await repo.get(monitor_id)).error == ""


async def test_delete(db, repo):
    monitor_id = await repo.create(_monitor())

    await repo.delete(monitor_id)

    assert await repo.get(monitor_id) is None
    assert await repo.list() == []
