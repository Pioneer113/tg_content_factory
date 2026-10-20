"""Tests for CLI common utilities."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import typer

from tg_agent.cli.commands.common import resolve_channel, resolve_date_bounds


def test_resolve_channel_by_pk():
    """Test resolve channel by primary key."""
    channels = [
        MagicMock(id=1, channel_id=100, username="channel1"),
        MagicMock(id=2, channel_id=200, username="channel2"),
    ]
    result = resolve_channel(channels, "1")
    assert result.id == 1


def test_resolve_channel_by_channel_id():
    """Test resolve channel by channel_id."""
    channels = [
        MagicMock(id=1, channel_id=100, username="channel1"),
        MagicMock(id=2, channel_id=200, username="channel2"),
    ]
    result = resolve_channel(channels, "200")
    assert result.channel_id == 200


def test_resolve_channel_by_username():
    """Test resolve channel by username."""
    channels = [
        MagicMock(id=1, channel_id=100, username="channel1"),
        MagicMock(id=2, channel_id=200, username="channel2"),
    ]
    result = resolve_channel(channels, "@channel1")
    assert result.username == "channel1"


def test_resolve_channel_by_username_without_at():
    """Test resolve channel by username without @."""
    channels = [
        MagicMock(id=1, channel_id=100, username="channel1"),
        MagicMock(id=2, channel_id=200, username="channel2"),
    ]
    result = resolve_channel(channels, "channel2")
    assert result.username == "channel2"


def test_resolve_channel_not_found():
    """Test resolve channel not found."""
    channels = [
        MagicMock(id=1, channel_id=100, username="channel1"),
    ]
    result = resolve_channel(channels, "999")
    assert result is None


def test_resolve_channel_username_not_found():
    """Test resolve channel by username not found."""
    channels = [
        MagicMock(id=1, channel_id=100, username="channel1"),
    ]
    result = resolve_channel(channels, "@nonexistent")
    assert result is None


def test_resolve_channel_case_insensitive():
    """Test resolve channel is case insensitive."""
    channels = [
        MagicMock(id=1, channel_id=100, username="TestChannel"),
    ]
    result = resolve_channel(channels, "@testchannel")
    assert result is not None


def test_resolve_channel_no_username():
    """Test resolve channel with no username."""
    channels = [
        MagicMock(id=1, channel_id=100, username=None),
    ]
    result = resolve_channel(channels, "@nonexistent")
    assert result is None


# resolve_date_bounds (#1593)


def test_resolve_date_bounds_normalizes_relaxed_dates():
    """2026-10-5 ≡ 2026-10-05: relaxed day/month get canonicalized (#1593)."""
    assert resolve_date_bounds("2026-10-5", "2026-10-9") == ("2026-10-05", "2026-10-09")


def test_resolve_date_bounds_passes_canonical_and_none_through():
    assert resolve_date_bounds("2026-10-05", None) == ("2026-10-05", None)


def test_resolve_date_bounds_rejects_garbage():
    """Non-date input becomes a usage error, never a silent wrong window (#1593)."""
    with pytest.raises(typer.BadParameter):
        resolve_date_bounds("yesterday", None)
    with pytest.raises(typer.BadParameter):
        resolve_date_bounds(None, "2026-13-45")
