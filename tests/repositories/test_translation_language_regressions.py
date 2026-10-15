"""A cached translation only satisfies requests for that destination language."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.models import Channel, Message

pytestmark = [pytest.mark.anyio, pytest.mark.aiosqlite_serial]


@pytest.mark.parametrize(
    ("translated_to", "requested", "needs_translation"),
    [
        pytest.param("de", "fr", True, id="custom-language-changed"),
        pytest.param("en", "fr", True, id="english-does-not-satisfy-french"),
        pytest.param("de", "de", False, id="same-language-is-cached"),
    ],
)
async def test_translation_cache_is_scoped_to_destination_language(db, translated_to, requested, needs_translation):
    await db.add_channel(Channel(channel_id=100, title="Source"))
    assert await db.insert_message(
        Message(channel_id=100, message_id=1, text="Привет, мир!", detected_lang="ru", date=datetime.now(timezone.utc))
    )
    messages = db.repos.messages
    pending = await messages.get_untranslated_messages(translated_to)
    assert len(pending) == 1
    message_id = pending[0].id
    await messages.update_translation(message_id, translated_to, "Saved translation")
    assert await messages.get_untranslated_messages(translated_to) == []

    pending = await messages.get_untranslated_messages(requested)

    assert [message.id for message in pending] == ([message_id] if needs_translation else [])
