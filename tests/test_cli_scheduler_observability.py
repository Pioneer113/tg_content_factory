"""CLI observability for the live worker (#1570).

Two blind spots fixed:

1. ``scheduler status`` only described configuration — it now renders the same
   ``runtime_snapshots`` the web side uses (worker_heartbeat, scheduler_status,
   scheduler_jobs, collection_queue_status), so it shows what the running
   worker actually does instead of what will happen on the next restart.
2. Raw ``settings set`` of keys the worker mutates at runtime wrote the DB row
   only; the live pause gate / scheduler intervals kept the old state. The set
   now also enqueues the corresponding telegram command (collection.pause/
   resume, scheduler.reconcile) — the same channel the web routes use.

Tests are fake-first: snapshots are seeded directly into the DB, no worker.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.cli.commands.scheduler import status_impl
from src.cli.commands.settings import set_impl
from src.config import AppConfig
from src.database import Database
from src.models import (
    RuntimeSnapshot,
    TelegramCommandStatus,
)

pytestmark = pytest.mark.aiosqlite_serial

_SECRET = "test-session-encryption-key"


@contextmanager
def _patched_init_db(db_path: str):
    async def fake_init_db(config_path: str):
        db = Database(db_path, session_encryption_secret=_SECRET)
        await db.initialize()
        return AppConfig(), db

    with patch("src.cli.runtime.init_db", side_effect=fake_init_db):
        yield


async def _open(db_path: str) -> Database:
    db = Database(db_path, session_encryption_secret=_SECRET)
    await db.initialize()
    return db


async def _seed_snapshots(db: Database) -> None:
    """Fake the four snapshots a live worker publishes (worker.py:_publish_snapshots)."""
    now = datetime.now(timezone.utc)
    upsert = db.repos.runtime_snapshots.upsert_snapshot
    await upsert(
        RuntimeSnapshot(
            snapshot_type="worker_heartbeat",
            payload={"status": "alive", "timestamp": now.isoformat()},
            updated_at=now,
        )
    )
    await upsert(
        RuntimeSnapshot(
            snapshot_type="scheduler_status",
            payload={"is_running": True, "interval_minutes": 45},
        )
    )
    await upsert(
        RuntimeSnapshot(
            snapshot_type="scheduler_jobs",
            payload={
                "jobs": [
                    {"job_id": "collect_all", "next_run": "2026-10-17T12:00:00+00:00"},
                    {"job_id": "warm_all_dialogs", "next_run": None},
                ]
            },
        )
    )
    await upsert(
        RuntimeSnapshot(
            snapshot_type="collection_queue_status",
            payload={
                "paused": True,
                "running_count": 1,
                "target_worker_count": 2,
                "current_task_id": 7,
            },
        )
    )


# ---------------------------------------------------------------------------
# scheduler status reads runtime_snapshots
# ---------------------------------------------------------------------------


async def test_status_shows_live_worker_state(tmp_path, capsys):
    db_path = str(tmp_path / "impl.db")
    seed_db = await _open(db_path)
    try:
        await _seed_snapshots(seed_db)
    finally:
        await seed_db.close()

    with _patched_init_db(db_path):
        await status_impl("config.yaml")

    out = capsys.readouterr().out
    assert "Worker: alive" in out
    assert "Running: yes" in out
    assert "Interval: 45 min" in out
    assert "paused=yes" in out
    assert "running=1/2" in out
    assert "collect_all: next run 2026-10-17T12:00:00+00:00" in out
    # Job without a next run must not fabricate a timestamp.
    assert "warm_all_dialogs: next run n/a" in out


async def test_status_without_worker_reports_down(tmp_path, capsys):
    db_path = str(tmp_path / "impl.db")

    with _patched_init_db(db_path):
        await status_impl("config.yaml")

    out = capsys.readouterr().out
    assert "Worker: down" in out
    assert "live state unavailable" in out
    # The config block is still the fallback when no worker publishes.
    assert "Scheduler config:" in out
    assert "Running:" not in out


async def test_status_stale_heartbeat_reports_down(tmp_path, capsys):
    db_path = str(tmp_path / "impl.db")
    seed_db = await _open(db_path)
    try:
        stale = datetime.now(timezone.utc) - timedelta(seconds=300)
        await seed_db.repos.runtime_snapshots.upsert_snapshot(
            RuntimeSnapshot(
                snapshot_type="worker_heartbeat",
                payload={"status": "alive"},
                updated_at=stale,
            )
        )
    finally:
        await seed_db.close()

    with _patched_init_db(db_path):
        await status_impl("config.yaml")

    out = capsys.readouterr().out
    assert "Worker: down (stale heartbeat" in out
    # Age is 300s + the time between seeding and the read — exact digits jitter.
    assert re.search(r"last \d+s ago", out)
    assert "Running:" not in out


# ---------------------------------------------------------------------------
# settings set of runtime-mutated keys reaches the worker
# ---------------------------------------------------------------------------

# (key, raw value, expected command, expected canonical value)
RUNTIME_KEYS = [
    ("collection_queue_paused", "1", "collection.pause", "1"),
    ("collection_queue_paused", "true", "collection.pause", "1"),
    ("collection_queue_paused", "yes", "collection.pause", "1"),
    ("collection_queue_paused", "0", "collection.resume", "0"),
    ("collection_queue_paused", "false", "collection.resume", "0"),
    ("collect_interval_minutes", "45", "scheduler.reconcile", "45"),
    ("warm_dialogs_interval_minutes", "60", "scheduler.reconcile", "60"),
    ("scheduler_autostart", "1", "scheduler.reconcile", "1"),
    ("scheduler_job_disabled:collect_all", "1", "scheduler.reconcile", "1"),
]


@pytest.mark.parametrize(("key", "value", "command", "canonical"), RUNTIME_KEYS)
async def test_settings_set_runtime_key_enqueues_command(
    tmp_path, capsys, key, value, command, canonical
):
    db_path = str(tmp_path / "impl.db")
    with _patched_init_db(db_path):
        await set_impl("config.yaml", key=key, value=value)

    assert "enqueued" in capsys.readouterr().out

    verify = await _open(db_path)
    try:
        assert await verify.get_setting(key) == canonical
        commands = await verify.repos.telegram_commands.list_commands(
            command_type=command, limit=10
        )
        assert commands, f"settings set {key} must enqueue {command} for the running worker"
        assert all(c.status == TelegramCommandStatus.PENDING for c in commands)
        assert all(c.requested_by == "cli:settings.set" for c in commands)
    finally:
        await verify.close()


async def test_settings_set_plain_key_writes_row_only(tmp_path, capsys):
    db_path = str(tmp_path / "impl.db")
    with _patched_init_db(db_path):
        await set_impl("config.yaml", key="some_ui_pref", value="bar")

    out = capsys.readouterr().out
    assert out.strip() == "Set some_ui_pref = bar"

    verify = await _open(db_path)
    try:
        assert await verify.get_setting("some_ui_pref") == "bar"
        commands = await verify.repos.telegram_commands.list_commands(limit=100)
        assert commands == [], "plain keys must not enqueue worker commands"
    finally:
        await verify.close()
