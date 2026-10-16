"""Dispatcher tests for auth.qr_* handlers (#1551): real DB, fake TelegramAuth.

The materialization path (require encryption key → Account insert → pool
connect) is the data-loss-sensitive part, so it runs against a real Database —
same pattern as the accounts_connect tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.database import Database
from src.models import Account, TelegramCommand, TelegramCommandStatus
from src.services.telegram_command_dispatcher import TelegramCommandDispatcher

BARE_PHONE = "79991234567"  # Telethon user.phone arrives without the leading +
PHONE = "+" + BARE_PHONE  # canonical stored form
SESSION_STR = "SECRET_QR_SESSION"


class FakeQrAuth:
    """Minimal TelegramAuth surface for the qr handlers (real contract shapes)."""

    is_configured = True

    def __init__(self):
        self.snapshots: dict[str, dict] = {}
        self.consumed: dict[str, dict] = {}
        self.password_results: dict[str, dict] = {}
        self.qr_start_calls = 0
        self.failures: list[tuple[str, str]] = []

    async def qr_start(self) -> dict:
        self.qr_start_calls += 1
        return {
            "qr_id": "q1",
            "url": "tg://login?token=AAA",
            "expires_at": "2026-10-16T00:00:30+00:00",
        }

    def qr_status(self, qr_id: str) -> dict | None:
        return self.snapshots.get(qr_id)

    def qr_fail(self, qr_id: str, error: str) -> None:
        self.failures.append((qr_id, error))
        snapshot = self.snapshots.get(qr_id)
        if snapshot is not None and snapshot.get("state") == "success":
            snapshot["state"] = "failed"
            snapshot["error"] = error

    async def consume_qr_session(self, qr_id: str) -> dict | None:
        return self.consumed.pop(qr_id, None)

    async def qr_password(self, qr_id: str, password: str) -> dict:
        return self.password_results.get(
            qr_id, {"state": "password_needed", "error": "invalid_password"}
        )


def _pool_mock() -> MagicMock:
    pool = MagicMock()
    pool.add_client = AsyncMock()
    pool.get_client_by_phone = AsyncMock(return_value=None)
    pool.release_client = AsyncMock()
    return pool


async def _db(tmp_path, *, encryption: bool = True) -> Database:
    # explicit None: an unset arg would fall back to SESSION_ENCRYPTION_KEY from env
    secret = "test-session-encryption-key" if encryption else None
    db = Database(str(tmp_path / "test.db"), session_encryption_secret=secret)
    await db.initialize()
    return db


async def test_qr_start_requires_auth(tmp_path):
    db = await _db(tmp_path)
    try:
        d = TelegramCommandDispatcher(db, _pool_mock(), auth=None)
        with pytest.raises(RuntimeError, match="auth_not_configured"):
            await d._handle_auth_qr_start({})
    finally:
        await db.close()


async def test_qr_start_fails_fast_without_encryption_key(tmp_path):
    """A deployment that cannot persist the session must not waste a scan (#1555 review)."""
    db = await _db(tmp_path, encryption=False)
    try:
        auth = FakeQrAuth()
        d = TelegramCommandDispatcher(db, _pool_mock(), auth=auth)
        with pytest.raises(RuntimeError, match="SESSION_ENCRYPTION_KEY"):
            await d._handle_auth_qr_start({})
        assert auth.qr_start_calls == 0
    finally:
        await db.close()


async def test_qr_start_returns_token(tmp_path):
    db = await _db(tmp_path)
    try:
        d = TelegramCommandDispatcher(db, _pool_mock(), auth=FakeQrAuth())
        result = await d._handle_auth_qr_start({})
        assert result["result"]["qr_id"] == "q1"
        assert result["result"]["url"] == "tg://login?token=AAA"
    finally:
        await db.close()


async def test_qr_status_unknown_session_raises(tmp_path):
    db = await _db(tmp_path)
    try:
        d = TelegramCommandDispatcher(db, _pool_mock(), auth=FakeQrAuth())
        with pytest.raises(RuntimeError, match="qr_session_not_found"):
            await d._handle_auth_qr_status({"qr_id": "ghost"})
    finally:
        await db.close()


async def test_qr_status_pending_passthrough_no_materialization(tmp_path):
    db = await _db(tmp_path)
    try:
        auth = FakeQrAuth()
        auth.snapshots["q1"] = {"qr_id": "q1", "state": "pending", "url": "tg://login?token=R1"}
        pool = _pool_mock()
        d = TelegramCommandDispatcher(db, pool, auth=auth)
        result = await d._handle_auth_qr_status({"qr_id": "q1"})
        assert result["result"]["state"] == "pending"
        assert await db.get_accounts() == []
        pool.add_client.assert_not_awaited()
    finally:
        await db.close()


async def test_qr_status_success_materializes_account_once(tmp_path):
    db = await _db(tmp_path)
    try:
        auth = FakeQrAuth()
        auth.snapshots["q1"] = {"qr_id": "q1", "state": "success", "phone": BARE_PHONE}
        auth.consumed["q1"] = {
            "qr_id": "q1",
            "state": "success",
            "phone": BARE_PHONE,  # bare, as Telethon reports it
            "session_string": SESSION_STR,
        }
        pool = _pool_mock()
        d = TelegramCommandDispatcher(db, pool, auth=auth)

        result = await d._handle_auth_qr_status({"qr_id": "q1"})
        assert result["result"]["state"] == "success"
        assert result["result"]["phone"] == PHONE  # normalized to canonical +E.164
        assert SESSION_STR not in str(result)  # secret never reaches the command result

        accounts = await db.get_accounts()
        assert len(accounts) == 1
        assert accounts[0].phone == PHONE
        assert accounts[0].is_primary is True  # first account becomes primary
        pool.add_client.assert_awaited_once_with(PHONE, SESSION_STR)

        # duplicate poll after consume: terminal state, no second account
        auth.snapshots["q1"] = {"qr_id": "q1", "state": "success", "phone": BARE_PHONE}
        result = await d._handle_auth_qr_status({"qr_id": "q1"})
        assert result["result"]["state"] == "consumed"
        assert len(await db.get_accounts()) == 1
        pool.add_client.assert_awaited_once()
    finally:
        await db.close()


async def test_qr_status_materialization_failure_marks_flow_failed(tmp_path):
    """Failed materialization after the one-shot consume must not look like success (#1555 review)."""
    db = await _db(tmp_path)
    try:
        auth = FakeQrAuth()
        auth.snapshots["q1"] = {"qr_id": "q1", "state": "success", "phone": BARE_PHONE}
        auth.consumed["q1"] = {
            "qr_id": "q1",
            "state": "success",
            "phone": BARE_PHONE,
            "session_string": SESSION_STR,
        }
        d = TelegramCommandDispatcher(db, _pool_mock(), auth=auth)
        db.add_account = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError, match="boom"):
            await d._handle_auth_qr_status({"qr_id": "q1"})
        assert auth.failures == [("q1", "qr_materialization_failed")]

        # the next poll reports the failure, not a fake success
        result = await d._handle_auth_qr_status({"qr_id": "q1"})
        assert result["result"]["state"] == "failed"
        assert result["result"]["error"] == "qr_materialization_failed"
    finally:
        await db.close()


async def test_qr_status_success_not_primary_when_account_exists(tmp_path):
    db = await _db(tmp_path)
    try:
        await db.add_account(Account(phone="+111", session_string="EXISTING", is_primary=True))
        auth = FakeQrAuth()
        auth.snapshots["q1"] = {"qr_id": "q1", "state": "success", "phone": BARE_PHONE}
        auth.consumed["q1"] = {
            "qr_id": "q1",
            "state": "success",
            "phone": BARE_PHONE,
            "session_string": SESSION_STR,
        }
        d = TelegramCommandDispatcher(db, _pool_mock(), auth=auth)
        result = await d._handle_auth_qr_status({"qr_id": "q1"})
        assert result["result"]["state"] == "success"
        accounts = await db.get_accounts()
        new = next(a for a in accounts if a.phone == PHONE)
        assert new.is_primary is False
    finally:
        await db.close()


async def test_qr_status_success_requires_session_encryption_key(tmp_path):
    db = await _db(tmp_path, encryption=False)
    try:
        auth = FakeQrAuth()
        auth.snapshots["q1"] = {"qr_id": "q1", "state": "success", "phone": BARE_PHONE}
        auth.consumed["q1"] = {
            "qr_id": "q1",
            "state": "success",
            "phone": BARE_PHONE,
            "session_string": SESSION_STR,
        }
        d = TelegramCommandDispatcher(db, _pool_mock(), auth=auth)
        with pytest.raises(RuntimeError, match="SESSION_ENCRYPTION_KEY"):
            await d._handle_auth_qr_status({"qr_id": "q1"})
        assert await db.get_accounts() == []
    finally:
        await db.close()


async def test_qr_password_invalid_keeps_state(tmp_path):
    db = await _db(tmp_path)
    try:
        auth = FakeQrAuth()
        auth.snapshots["q1"] = {"qr_id": "q1", "state": "password_needed"}
        d = TelegramCommandDispatcher(db, _pool_mock(), auth=auth)
        result = await d._handle_auth_qr_password({"qr_id": "q1", "password_2fa": "wrong"})
        assert result["result"]["state"] == "password_needed"
        assert result["result"]["error"] == "invalid_password"
        assert await db.get_accounts() == []
    finally:
        await db.close()


async def test_qr_password_scrubs_secret_from_persisted_payload(tmp_path):
    """The scrub must reach the DB row, not just the in-memory dict (#1555 review)."""
    db = await _db(tmp_path)
    try:
        auth = FakeQrAuth()
        auth.snapshots["q1"] = {"qr_id": "q1", "state": "password_needed"}
        command_id = await db.repos.telegram_commands.create_command(
            TelegramCommand(
                command_type="auth.qr_password",
                payload={"qr_id": "q1", "password_2fa": "topsecret"},
            )
        )
        d = TelegramCommandDispatcher(db, _pool_mock(), auth=auth)
        # one full claim → dispatch → update cycle, exactly like the worker loop
        await d._run_iteration()

        command = await db.repos.telegram_commands.get_command(command_id)
        assert command.status == TelegramCommandStatus.SUCCEEDED
        assert command.result_payload["state"] == "password_needed"
        assert command.payload["password_2fa"] == ""
        assert "topsecret" not in (command.payload or {})
    finally:
        await db.close()


async def test_qr_password_success_materializes_account(tmp_path):
    db = await _db(tmp_path)
    try:
        auth = FakeQrAuth()
        auth.password_results["q1"] = {"state": "success"}
        auth.snapshots["q1"] = {"qr_id": "q1", "state": "success", "phone": BARE_PHONE}
        auth.consumed["q1"] = {
            "qr_id": "q1",
            "state": "success",
            "phone": BARE_PHONE,
            "session_string": SESSION_STR,
        }
        pool = _pool_mock()
        d = TelegramCommandDispatcher(db, pool, auth=auth)
        result = await d._handle_auth_qr_password({"qr_id": "q1", "password_2fa": "right"})
        assert result["result"]["state"] == "success"
        assert result["result"]["phone"] == PHONE
        assert len(await db.get_accounts()) == 1
        pool.add_client.assert_awaited_once_with(PHONE, SESSION_STR)
    finally:
        await db.close()
