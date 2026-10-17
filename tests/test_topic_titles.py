"""Заголовки форум-топиков при чтении сообщений (#1552).

Кэш ``forum_topics`` заполняет коллектор; выдача (репозиторий → CLI/web/agent)
приклеивает ``topic_title`` одним батч-SELECT'ом и не ходит в сеть.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.cli.commands.messages import _print_messages
from src.database import Database
from src.database.repositories.messages import MessageSearchPage, MessagesRepository
from src.models import Message
from tests.agent_tools_helpers import _get_tool_handlers, _text

FORUM_CID = 100500
NOW = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)


def _msg(message_id: int, topic_id: int | None) -> Message:
    return Message(
        channel_id=FORUM_CID,
        message_id=message_id,
        text=f"msg {message_id}",
        topic_id=topic_id,
        date=NOW,
    )


async def _seed_forum(db: Database) -> None:
    """Канал + кэш топиков + сообщения: два в topic 1, одно в topic 2, одно без."""
    await db.upsert_forum_topics(
        FORUM_CID,
        [{"id": 1, "title": "General"}, {"id": 2, "title": "Новости"}],
    )
    for mid, tid in [(11, 1), (12, 1), (13, 2), (14, None)]:
        await db.insert_message(_msg(mid, tid))


def _spy_forum_topic_queries(db: Database) -> list[int]:
    """Счётчик SELECT'ов по forum_topics на уровне репозитория (ReadConnection)."""
    counter: list[int] = []
    repo_db = db.repos.messages._db
    original = repo_db.execute

    async def _counting(sql, params=()):
        if "FROM forum_topics" in sql:
            counter.append(1)
        return await original(sql, params)

    repo_db.execute = _counting  # type: ignore[method-assign]
    return counter


# --- Репозиторий: приклейка при выдаче ---------------------------------------


class TestAttachTopicTitles:
    @pytest.mark.anyio
    async def test_search_messages_attaches_titles(self, db):
        await _seed_forum(db)
        page = await db.search_messages(channel_id=FORUM_CID)
        by_mid = {m.message_id: m for m in page.messages}
        assert by_mid[11].topic_title == "General"
        assert by_mid[12].topic_title == "General"
        assert by_mid[13].topic_title == "Новости"
        assert by_mid[14].topic_title is None

    @pytest.mark.anyio
    async def test_unknown_topic_id_stays_none(self, db):
        await _seed_forum(db)
        await db.insert_message(_msg(99, 4242))
        page = await db.search_messages(channel_id=FORUM_CID)
        by_mid = {m.message_id: m for m in page.messages}
        assert by_mid[99].topic_title is None  # выдача не падает, заголовка нет

    @pytest.mark.anyio
    async def test_one_batch_query_per_page(self, db):
        await _seed_forum(db)
        counter = _spy_forum_topic_queries(db)
        await db.search_messages(channel_id=FORUM_CID)
        assert len(counter) == 1  # N сообщений одного топика → один батч-запрос

    @pytest.mark.anyio
    async def test_no_topic_messages_no_query(self, db):
        await db.insert_message(_msg(21, None))
        counter = _spy_forum_topic_queries(db)
        page = await db.search_messages(channel_id=FORUM_CID)
        assert page.messages
        assert counter == []  # не-форумная выдача — прежнее поведение, без запросов

    @pytest.mark.anyio
    async def test_load_messages_by_ids_attaches(self, db):
        await _seed_forum(db)
        repo = MessagesRepository(db)
        page = await db.search_messages(channel_id=FORUM_CID)
        ids = [m.id for m in page.messages if m.message_id in (11, 13)]
        loaded = await repo._load_messages_by_ids(ids)  # semantic/hybrid путь
        by_mid = {m.message_id: m for m in loaded}
        assert by_mid[11].topic_title == "General"
        assert by_mid[13].topic_title == "Новости"


# --- CLI: text / json / csv ---------------------------------------------------


class TestCliRender:
    def test_text_shows_topic_title(self, capsys):
        msgs = [_msg(11, 1), _msg(14, None)]
        msgs[0].topic_title = "General"
        _print_messages(msgs, "text", 2)
        out = capsys.readouterr().out
        assert "topic=1 (General)" in out
        assert "#14" in out  # сообщение без топика рендерится как раньше

    def test_text_unknown_title_bare_id(self, capsys):
        _print_messages([_msg(11, 1)], "text", 1)
        out = capsys.readouterr().out
        assert "topic=1" in out
        assert "topic=1 (" not in out

    def test_json_topic_keys(self, capsys):
        msg = _msg(11, 1)
        msg.topic_title = "General"
        _print_messages([msg], "json", 1)
        items = json.loads(capsys.readouterr().out)
        assert items[0]["topic_id"] == 1
        assert items[0]["topic_title"] == "General"

    def test_csv_topic_columns(self, capsys):
        msg = _msg(11, 1)
        msg.topic_title = "General"
        _print_messages([msg], "csv", 1)
        rows = list(csv.reader(capsys.readouterr().out.splitlines()))
        assert rows[0][5:7] == ["topic_id", "topic_title"]
        assert rows[1][5:7] == ["1", "General"]


# --- Agent tools --------------------------------------------------------------


class TestAgentToolSearchMessages:
    @pytest.mark.anyio
    async def test_search_result_shows_topic_title(self):
        msg = _msg(11, 1)
        msg.topic_title = "General"
        mock_db = MagicMock(spec=Database)
        mock_db.search_messages = AsyncMock(
            return_value=MessageSearchPage(messages=[msg], total=1)
        )
        handlers = _get_tool_handlers(mock_db)
        result = await handlers["search_messages"]({"query": "msg"})
        text = _text(result)
        assert 'topic=1 "General"' in text

    @pytest.mark.anyio
    async def test_search_result_unknown_title_bare_id(self):
        mock_db = MagicMock(spec=Database)
        mock_db.search_messages = AsyncMock(
            return_value=MessageSearchPage(messages=[_msg(11, 1)], total=1)
        )
        handlers = _get_tool_handlers(mock_db)
        result = await handlers["search_messages"]({"query": "msg"})
        assert "topic=1" in _text(result)
        assert 'topic=1 "' not in _text(result)
