"""Tests for collector cancellation isolation + flood handling (audit #835/6, #835/16)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telethon_floodgate import FloodWaitInfo, HandledFloodWaitError

from tg_agent.config import SchedulerConfig
from tg_agent.telegram.collector import Collector


def _collector() -> Collector:
    return Collector(MagicMock(), MagicMock(), SchedulerConfig())


@pytest.mark.anyio
async def test_cancel_stats_does_not_cancel_channel_collection():
    """STATS_ALL cancel must not abort in-flight channel collection (audit #835/6)."""
    c = _collector()
    await c.cancel_stats()
    assert c._is_stats_cancelled() is True
    assert c._is_collection_cancelled() is False
    assert c.is_cancelled is False


@pytest.mark.anyio
async def test_global_cancel_stops_both_collection_and_stats():
    c = _collector()
    await c.cancel()
    assert c._is_collection_cancelled() is True
    assert c._is_stats_cancelled() is True
    assert c.is_cancelled is True


@pytest.mark.anyio
async def test_discover_phone_handles_handled_flood_wait():
    """resolve raises HandledFloodWaitError (not raw FloodWaitError) — the handler
    must catch it and move on, not crash on a dead branch (audit #835/16)."""
    c = _collector()
    pool = c._pool
    pool.connected_phones = MagicMock(return_value={"+1"})
    session = MagicMock()
    info = FloodWaitInfo(
        operation="resolve",
        phone="+1",
        wait_seconds=5,
        next_available_at_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        detail="flood 5s",
    )
    session.resolve_entity = AsyncMock(side_effect=HandledFloodWaitError(info))
    pool.get_client_by_phone = AsyncMock(return_value=(session, "+1"))
    pool.is_dialogs_fetched = MagicMock(return_value=True)
    pool.release_client = AsyncMock()

    with patch("tg_agent.telegram.collector_mixins.collection.adapt_transport_session", lambda s, **k: s):
        result = await c._discover_phone_for_channel(123, exclude="+2")

    assert result is None
    pool.release_client.assert_awaited()


# ------------------------------------------------------------------
# Mutation slice #5 killer tests: stream + release contracts
# ------------------------------------------------------------------


def _fake_msg(mid: int):
    # message= pins the real Telethon contract (raw str | None): without it a
    # MagicMock auto-generates a truthy non-string `message`, which the
    # shared text/entities helper (#1547) correctly refuses.
    m = MagicMock(id=mid, text=f"msg {mid}", message=f"msg {mid}", date=datetime.now(timezone.utc))
    m.sender.first_name = "F"
    m.sender.last_name = "L"
    return m


def _empty_agen():
    async def _agen():
        if False:  # pragma: no cover — an immediately exhausted stream
            yield

    return _agen()


@pytest.mark.anyio
async def test_stream_passes_incremental_args_to_stream_messages():
    """The stream must be opened reverse=True with the incremental min_id and
    the pacing wait_time — dropping any of them silently breaks incremental
    collection (#mutation slice 5)."""
    from tg_agent.telegram.collector_types import _StreamOutcome

    c = _collector()
    session = MagicMock()
    session.stream_messages = MagicMock(return_value=_empty_agen())
    outcome = _StreamOutcome()

    await c._stream_channel_messages(
        session=session,
        entity="ENT",
        min_id=5,
        limit=50,
        channel=MagicMock(),
        channel_id=123,
        cancel_event=None,
        messages_batch=[],
        flush_batch=AsyncMock(return_value=True),
        outcome=outcome,
    )

    session.stream_messages.assert_called_once_with(
        "ENT",
        min_id=5,
        limit=50,
        reverse=True,
        wait_time=c._config.delay_between_requests_sec,
    )
    assert outcome.stop_due_to_persistence_error is False


@pytest.mark.anyio
async def test_stream_flush_failure_sets_persistence_stop_and_keeps_batch():
    """A failed flush flags the outcome (the pass stops) but does NOT clear the
    batch — partial progress survives for the next cycle (#mutation slice 5)."""
    from tg_agent.telegram.collector_mixins.stream import MESSAGE_FLUSH_BATCH_SIZE
    from tg_agent.telegram.collector_types import _StreamOutcome

    c = _collector()
    c._channel_still_exists = AsyncMock(return_value=True)
    session = MagicMock()
    msgs = [_fake_msg(i) for i in range(MESSAGE_FLUSH_BATCH_SIZE + 2)]

    async def agen():
        for m in msgs:
            yield m

    session.stream_messages = MagicMock(return_value=agen())
    flush = AsyncMock(return_value=False)
    batch: list = []
    outcome = _StreamOutcome()

    await c._stream_channel_messages(
        session=session,
        entity="ENT",
        min_id=0,
        limit=None,
        channel=MagicMock(),
        channel_id=123,
        cancel_event=None,
        messages_batch=batch,
        flush_batch=flush,
        outcome=outcome,
    )

    assert outcome.stop_due_to_persistence_error is True
    assert flush.await_count == 1
    assert len(batch) == MESSAGE_FLUSH_BATCH_SIZE


@pytest.mark.anyio
async def test_stream_close_awaits_aclose_under_cleanup_timeout():
    """The stream's aclose runs under the bounded cleanup timeout — an unbounded
    or dropped close leaks the async generator (#mutation slice 5)."""
    from inspect import isawaitable

    from tg_agent.telegram.collector_types import _StreamOutcome

    c = _collector()
    session = MagicMock()
    session.stream_messages = MagicMock(return_value=_empty_agen())

    timeouts = []

    async def _wait_for(aw, *, timeout=None):
        # Await what we're given: swallowing the aclose coroutine would leak a
        # RuntimeWarning that lands on whichever test runs next.
        timeouts.append(timeout)
        if isawaitable(aw):
            await aw
        return None

    with patch("asyncio.wait_for", _wait_for):
        await c._stream_channel_messages(
            session=session,
            entity="ENT",
            min_id=0,
            limit=None,
            channel=MagicMock(),
            channel_id=123,
            cancel_event=None,
            messages_batch=[],
            flush_batch=AsyncMock(return_value=True),
            outcome=_StreamOutcome(),
        )

    assert timeouts == [10.0]


@pytest.mark.anyio
async def test_release_collection_client_default_releases_without_retiring():
    """The default release returns the client to the pool — only an explicitly
    dirty stream retires it (#mutation slice 5)."""
    c = _collector()

    class _PoolWithRemove:
        def __init__(self):
            # A real instance attribute: the retire guard looks for
            # "remove_client" in pool.__dict__, which MagicMock does not fill.
            self.remove_client = MagicMock(return_value=None)
            self.release_client = AsyncMock()

    pool = _PoolWithRemove()
    c._pool = pool
    session = MagicMock()

    await c._release_collection_client("+1", session)

    pool.release_client.assert_awaited_once_with("+1")
    pool.remove_client.assert_not_called()
    session.raw_client.disconnect.assert_not_called()


@pytest.mark.anyio
async def test_release_collection_client_retire_removes_without_releasing():
    """A retire removes the dirty client from the pool and returns WITHOUT the
    plain release (the client is gone, releasing it would hand out a zombie
    (#mutation slice 5))."""
    c = _collector()

    class _PoolWithRemove:
        def __init__(self):
            self.remove_client = MagicMock(return_value=None)
            self.release_client = AsyncMock()

    pool = _PoolWithRemove()
    c._pool = pool

    await c._release_collection_client("+1", MagicMock(), retire=True)

    pool.remove_client.assert_called_once_with("+1")
    pool.release_client.assert_not_awaited()


@pytest.mark.anyio
async def test_release_collection_client_retire_disconnect_fallback():
    """Without a remove_client hook the retire disconnects the raw client under
    the bounded timeout, then releases the lease (#mutation slice 5)."""
    c = _collector()
    c._pool = MagicMock(spec=["release_client"])
    c._pool.release_client = AsyncMock()
    raw = MagicMock()

    async def _disc():
        return None

    raw.disconnect = MagicMock(side_effect=_disc)
    session = MagicMock()
    session.raw_client = raw

    timeouts = []

    async def _wait_for(aw, *, timeout=None):
        timeouts.append(timeout)
        await aw
        return None

    with patch("asyncio.wait_for", _wait_for):
        await c._release_collection_client("+1", session, retire=True)

    raw.disconnect.assert_called_once()
    assert timeouts == [10.0]
    c._pool.release_client.assert_awaited_once_with("+1")
