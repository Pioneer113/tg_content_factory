"""Portable semantic search must apply filters without losing indexed messages."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tg_agent.database.bundles import SearchBundle
from tg_agent.models import Channel, Message
from tg_agent.search.local_search import LocalSearch

pytestmark = [pytest.mark.anyio, pytest.mark.aiosqlite_serial]
MESSAGE_DATE = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


@pytest.fixture
def semantic_search(db):
    bundle = replace(SearchBundle.from_database(db), vec_available=False)
    return LocalSearch(bundle, SimpleNamespace(embed_query=AsyncMock(return_value=[1.0, 0.0])))


async def _index_messages(db, messages, vectors):
    for channel_id in {message.channel_id for message in messages}:
        await db.add_channel(Channel(channel_id=channel_id, title=f"Channel {channel_id}"))
    assert await db.insert_messages_batch(messages) == len(messages)
    rows = await db.repos.messages.get_messages_for_embedding(limit=len(messages))
    embeddings = [(message_id, vector) for (message_id, _), vector in zip(rows, vectors, strict=True)]
    await db.repos.messages.upsert_message_embeddings(embeddings)
    await db.repos.messages.upsert_message_embedding_json(embeddings)


async def test_channel_filter_is_applied_before_semantic_candidate_limit(db, semantic_search):
    messages = [
        Message(channel_id=100, message_id=index + 1, text=f"Noise {index}", date=MESSAGE_DATE) for index in range(201)
    ]
    messages.append(Message(channel_id=200, message_id=1, text="Wanted", date=MESSAGE_DATE))
    await _index_messages(db, messages, [[1.0, 0.0]] * 201 + [[0.8, 0.6]])

    small_page = await semantic_search.search_semantic("Query", channel_id=200, limit=1)
    large_page = await semantic_search.search_semantic("Query", channel_id=200, limit=100)

    assert [message.text for message in large_page.messages] == ["Wanted"]
    assert [message.text for message in small_page.messages] == ["Wanted"]


@pytest.mark.parametrize("include_filtered", [False, True], ids=["exclude-filtered", "include-filtered"])
async def test_semantic_search_honors_include_filtered(db, semantic_search, include_filtered):
    await _index_messages(db, [Message(channel_id=100, message_id=1, text="Wanted", date=MESSAGE_DATE)], [[1.0, 0.0]])
    await db.execute_write("UPDATE channels SET is_filtered = 1 WHERE channel_id = 100")
    assert await db.repos.messages.count_embeddings() == 1

    result = await semantic_search.search_semantic("Query", include_filtered=include_filtered)

    assert [message.text for message in result.messages] == (["Wanted"] if include_filtered else [])


@pytest.mark.parametrize(
    ("bounds", "expected"),
    [
        pytest.param({"date_from": "2026-10-14T10:00:00+00:00"}, ["Noon"], id="after-ten"),
        pytest.param({"date_to": "2026-10-14T10:00:00+00:00"}, [], id="before-ten"),
        pytest.param({"date_from": "2026-10-14"}, ["Noon"], id="date-only-from"),
        pytest.param({"date_to": "2026-10-14"}, ["Noon"], id="date-only-to-inclusive"),
        # Non-UTC offset: 13:00+03:00 == 10:00 UTC, so noon is after the bound.
        # A string comparison of the normalized forms would drop the message.
        pytest.param({"date_from": "2026-10-14T13:00:00+03:00"}, ["Noon"], id="tz-offset-from"),
    ],
)
async def test_semantic_search_compares_datetime_bounds_chronologically(db, semantic_search, bounds, expected):
    await _index_messages(db, [Message(channel_id=100, message_id=1, text="Noon", date=MESSAGE_DATE)], [[1.0, 0.0]])

    result = await semantic_search.search_semantic("Query", **bounds)

    assert [message.text for message in result.messages] == expected
