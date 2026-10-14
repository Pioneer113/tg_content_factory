from __future__ import annotations

from types import SimpleNamespace

import pytest
from telethon.tl.types import ReactionPaid

from src.telegram.reactions import (
    SUPPORTED_REACTION_EMOJIS_DISPLAY,
    TelegramReactionInvalidError,
    extract_message_reactions,
    extract_message_reactions_json,
    extract_reaction_users,
    fetch_message_reaction_users,
    format_message_reactions,
    format_reaction_counts,
    format_reaction_users,
    format_reaction_users_result,
    format_reactions_json,
    normalize_outgoing_reaction_emoji,
    normalize_reaction_users_limit,
    parse_reactions_json,
)
from tests.helpers import make_mock_reactions


def test_extract_message_reactions_none():
    msg = SimpleNamespace(reactions=None)

    assert extract_message_reactions(msg) == []
    assert extract_message_reactions_json(msg) is None
    assert format_message_reactions(msg) == ""


def test_normalize_outgoing_reaction_accepts_supported_and_strips_variation_selector():
    assert normalize_outgoing_reaction_emoji(" ❤️ ") == "❤"
    assert normalize_outgoing_reaction_emoji("✍️") == "✍"
    assert normalize_outgoing_reaction_emoji(None, allow_clear=True) is None


@pytest.mark.parametrize("emoji", ["✅", "ok", "👍 ✅", ""])
def test_normalize_outgoing_reaction_rejects_unsupported(emoji):
    with pytest.raises(TelegramReactionInvalidError) as exc_info:
        normalize_outgoing_reaction_emoji(emoji)

    assert repr(emoji) in str(exc_info.value)
    assert SUPPORTED_REACTION_EMOJIS_DISPLAY in str(exc_info.value)


def test_extract_message_reactions_multiple_emoji():
    msg = SimpleNamespace(reactions=make_mock_reactions([("👍", 5), ("❤️", 2)]))

    assert extract_message_reactions(msg) == [
        {"emoji": "👍", "count": 5},
        {"emoji": "❤️", "count": 2},
    ]
    assert format_message_reactions(msg) == "👍 5 ❤️ 2"


def test_extract_message_reactions_custom_emoji():
    msg = SimpleNamespace(reactions=make_mock_reactions([(12345678, 2)]))

    assert extract_message_reactions(msg) == [{"emoji": "custom:12345678", "count": 2}]
    assert format_message_reactions(msg) == "custom:12345678 2"


def test_extract_message_reactions_paid_and_unknown():
    msg = SimpleNamespace(
        reactions=SimpleNamespace(
            results=[
                SimpleNamespace(reaction=ReactionPaid(), count=4),
                SimpleNamespace(reaction=SimpleNamespace(), count=9),
            ]
        )
    )

    assert extract_message_reactions(msg) == [{"emoji": "paid", "count": 4}]
    assert format_message_reactions(msg) == "paid 4"


def test_parse_reactions_json_valid_and_format():
    items = parse_reactions_json('[{"emoji": "🔥", "count": 7}, {"emoji": "custom:42", "count": "3"}]')

    assert items == [{"emoji": "🔥", "count": 7}, {"emoji": "custom:42", "count": 3}]
    assert format_reaction_counts(items) == "🔥 7 custom:42 3"
    assert format_reactions_json('[{"emoji": "🔥", "count": 7}]') == "🔥 7"


def test_parse_reactions_json_invalid():
    assert parse_reactions_json("not json") == []
    assert parse_reactions_json(None) == []
    assert parse_reactions_json('{"emoji": "👍", "count": 1}') == []


def test_extract_reaction_users_groups_users_chats_custom_and_paid():
    result = SimpleNamespace(
        reactions=[
            SimpleNamespace(
                peer_id=SimpleNamespace(user_id=10),
                reaction=SimpleNamespace(emoticon="👍"),
            ),
            SimpleNamespace(
                peer_id=SimpleNamespace(user_id=11),
                reaction=SimpleNamespace(document_id=42),
            ),
            SimpleNamespace(
                peer_id=SimpleNamespace(channel_id=12),
                reaction=ReactionPaid(),
            ),
            SimpleNamespace(
                peer_id=SimpleNamespace(user_id=13),
                reaction=SimpleNamespace(),
            ),
        ],
        users=[
            SimpleNamespace(id=10, username="ivan", first_name="Ivan", last_name="Petrov"),
            SimpleNamespace(id=11, username=None, first_name="Maria", last_name=""),
        ],
        chats=[SimpleNamespace(id=12, title="Channel Reactor")],
    )

    items = extract_reaction_users(result)

    assert items == [
        {"emoji": "👍", "display": "@ivan", "peer_id": 10},
        {"emoji": "custom:42", "display": "Maria", "peer_id": 11},
        {"emoji": "paid", "display": "Channel Reactor", "peer_id": 12},
    ]
    assert format_reaction_users(items) == "👍 @ivan; custom:42 Maria; paid Channel Reactor"


def test_extract_reaction_users_falls_back_to_peer_id():
    result = SimpleNamespace(
        reactions=[
            SimpleNamespace(peer_id=SimpleNamespace(user_id=55), reaction=SimpleNamespace(emoticon="❤️")),
        ],
        users=[],
        chats=[],
    )

    assert extract_reaction_users(result) == [{"emoji": "❤️", "display": "id=55", "peer_id": 55}]


def test_format_reaction_users_unavailable_and_limited():
    assert format_reaction_users([], unavailable=True) == "пользователи реакций недоступны"
    assert format_reaction_users([{"emoji": "👍", "display": "@ivan"}], limited=True) == "👍 @ivan ..."


def test_normalize_reaction_users_limit():
    assert normalize_reaction_users_limit(None) == 20
    assert normalize_reaction_users_limit("3") == 3
    assert normalize_reaction_users_limit(0) == 1
    assert normalize_reaction_users_limit(999) == 100


@pytest.mark.anyio
async def test_fetch_message_reaction_users_invokes_telethon_request():
    class FakeClient:
        request = None

        async def __call__(self, request):
            self.request = request
            return SimpleNamespace(
                count=2,
                next_offset="next",
                reactions=[
                    SimpleNamespace(
                        peer_id=SimpleNamespace(user_id=10),
                        reaction=SimpleNamespace(emoticon="👍"),
                    ),
                ],
                users=[SimpleNamespace(id=10, username="ivan")],
                chats=[],
            )

    client = FakeClient()
    result = await fetch_message_reaction_users(client, "peer", 123, limit=500)

    assert client.request.peer == "peer"
    assert client.request.id == 123
    assert client.request.limit == 100
    assert result.items == [{"emoji": "👍", "display": "@ivan", "peer_id": 10}]
    assert result.limited is True
    assert format_reaction_users_result(result) == "👍 @ivan ..."


@pytest.mark.anyio
async def test_fetch_message_reaction_users_returns_unavailable_on_error():
    class FakeClient:
        async def __call__(self, request):
            raise RuntimeError("forbidden")

    result = await fetch_message_reaction_users(FakeClient(), "peer", 123)

    assert result.items == []
    assert result.unavailable == "forbidden"
    assert format_reaction_users_result(result) == "пользователи реакций недоступны"


# ------------------------------------------------------------------
# Mutation slice #6 killer tests: reaction-user extraction contracts
# ------------------------------------------------------------------

from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

from src.telegram.reactions import ReactionUsersResult  # noqa: E402


def test_peer_key_maps_all_peer_shapes():
    """Peer id objects resolve to (kind, id) in user → channel → chat → bare
    order (#mutation slice 6)."""
    from src.telegram.reactions import _peer_key

    assert _peer_key(SimpleNamespace(user_id=5)) == ("user", 5)
    assert _peer_key(SimpleNamespace(channel_id=-100)) == ("channel", -100)
    assert _peer_key(SimpleNamespace(chat_id=7)) == ("chat", 7)
    assert _peer_key(SimpleNamespace(id=9)) == (None, 9)
    assert _peer_key(SimpleNamespace()) == (None, None)


def test_display_peer_prefers_username_then_name_then_title():
    """@usernames are normalized, then full name, then channel title, then the
    bare id, then 'unknown' (#mutation slice 6)."""
    from src.telegram.reactions import _display_peer

    assert _display_peer(SimpleNamespace(username=" @ann "), 5) == "@ann"
    assert _display_peer(SimpleNamespace(first_name="Ann", last_name="Lee"), 5) == "Ann Lee"
    assert _display_peer(SimpleNamespace(title="My Channel"), 5) == "My Channel"
    assert _display_peer(None, 5) == "id=5"
    assert _display_peer(None, None) == "unknown"


def test_extract_reaction_users_routes_peer_kinds_with_entity_fallback():
    """user reactions resolve via the users index, chat/channel via chats, and
    a bare peer falls back across both (#mutation slice 6)."""
    result = SimpleNamespace(
        reactions=[
            SimpleNamespace(reaction=SimpleNamespace(emoticon="❤"), peer_id=SimpleNamespace(user_id=1)),
            SimpleNamespace(reaction=SimpleNamespace(emoticon="👍"), peer_id=SimpleNamespace(channel_id=2)),
            SimpleNamespace(reaction=SimpleNamespace(emoticon="🔥"), peer_id=SimpleNamespace(id=3)),
            SimpleNamespace(reaction=None, peer_id=SimpleNamespace(user_id=4)),
        ],
        users=[SimpleNamespace(id=1, username="ann"), SimpleNamespace(id=3, username="bare")],
        chats=[SimpleNamespace(id=2, title="Chan")],
    )
    items = extract_reaction_users(result)
    assert items == [
        {"emoji": "❤", "display": "@ann", "peer_id": 1},
        {"emoji": "👍", "display": "Chan", "peer_id": 2},
        {"emoji": "🔥", "display": "@bare", "peer_id": 3},
    ]


@pytest.mark.anyio
async def test_fetch_reaction_users_flood_propagates_and_failure_degrades():
    """FloodWait propagates to the caller (dispatcher owns the backoff); any
    other failure degrades to an unavailable result (#mutation slice 6)."""
    from telethon.errors import FloodWaitError

    with patch("src.telegram.backends.fetch_message_reaction_users_raw", AsyncMock()):
        with patch(
            "src.telegram.backends.fetch_message_reaction_users_raw",
            AsyncMock(side_effect=FloodWaitError(request=None)),
        ):
            with pytest.raises(FloodWaitError):
                await fetch_message_reaction_users(MagicMock(), "@chan", 5)

        with patch(
            "src.telegram.backends.fetch_message_reaction_users_raw",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            degraded = await fetch_message_reaction_users(MagicMock(), "@chan", 5)
    assert degraded == ReactionUsersResult([], unavailable="boom")


@pytest.mark.anyio
async def test_fetch_reaction_users_limited_flag_and_count_fallback():
    """limited is set by an explicit next_offset OR by total count exceeding the
    returned items; a missing count falls back to len(items) so limited stays
    off (#mutation slice 6)."""
    raw = SimpleNamespace(
        reactions=[SimpleNamespace(reaction=SimpleNamespace(emoticon="❤"), peer_id=SimpleNamespace(user_id=1))],
        users=[SimpleNamespace(id=1, username="ann")],
        chats=[],
        count=50,
        next_offset="offset-2",
    )
    with patch("src.telegram.backends.fetch_message_reaction_users_raw", AsyncMock(return_value=raw)):
        result = await fetch_message_reaction_users(MagicMock(), "@chan", 5)
    assert result.limited is True

    raw_offset_only = SimpleNamespace(
        reactions=[SimpleNamespace(reaction=SimpleNamespace(emoticon="❤"), peer_id=SimpleNamespace(user_id=1))],
        users=[SimpleNamespace(id=1, username="ann")],
        chats=[],
        count=None,
        next_offset="offset-3",
    )
    with patch(
        "src.telegram.backends.fetch_message_reaction_users_raw",
        AsyncMock(return_value=raw_offset_only),
    ):
        result = await fetch_message_reaction_users(MagicMock(), "@chan", 5)
    assert result.limited is True  # the offset alone must flag the result as limited

    raw_no_offset = SimpleNamespace(
        reactions=[SimpleNamespace(reaction=SimpleNamespace(emoticon="❤"), peer_id=SimpleNamespace(user_id=1))],
        users=[SimpleNamespace(id=1, username="ann")],
        chats=[],
        count=None,
        next_offset=None,
    )
    with patch(
        "src.telegram.backends.fetch_message_reaction_users_raw",
        AsyncMock(return_value=raw_no_offset),
    ):
        result = await fetch_message_reaction_users(MagicMock(), "@chan", 5)
    assert result.limited is False
    assert len(result.items) == 1
