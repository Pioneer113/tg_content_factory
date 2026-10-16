"""Черновики ответов на DM: атомарный клейм против двойной отправки (#1429).

Три слоя, fake-first (без живого Telegram):
- репозиторий: статусная машина и клейм-троица на реальном SQL (:memory:);
- хендлер воркера: клейм → отправка через фейк TelegramActionService → mark_sent;
- CLI: только постановка команды в очередь, никакой прямой отправки.

Ключевые приёмо-сдаточные инварианты эпика #1416 этапа 2.4: двойной
конкурентный клейм отправляет ровно один раз; апрув после отправки — no-op;
отклонённый черновик не отправляется никогда.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import typer

from src.cli.commands import dm as dm_cli
from src.config import AppConfig
from src.models import IncomingDm, TelegramCommandStatus
from src.services.dispatcher._errors import TelegramCommandRetryLaterError
from src.services.telegram_command_dispatcher import TelegramCommandDispatcher


async def _make_draft(db, *, status: str = "draft", incoming_dm_id: int = 1) -> int:
    """Черновик + журнальная строка; статус поднимается только легальными API."""
    await db.repos.incoming_dms.record(
        IncomingDm(
            phone="+10000000001",
            chat_id=555 + incoming_dm_id,
            message_id=incoming_dm_id,
            text="привет",
        )
    )
    draft_id, _ = await db.repos.dm_reply_drafts.create_draft(
        incoming_dm_id=incoming_dm_id,
        phone="+10000000001",
        chat_id=555 + incoming_dm_id,
        reply_text="ответ",
    )
    if status in ("approved", "sending"):
        await db.repos.dm_reply_drafts.decide(draft_id, "approved")
    if status == "rejected":
        await db.repos.dm_reply_drafts.decide(draft_id, "rejected")
    elif status == "sending":
        assert await db.repos.dm_reply_drafts.claim_for_send(draft_id)
    return draft_id


def _mock_pool():
    pool = MagicMock()
    pool.clients = {}
    pool.disconnect_all = AsyncMock()
    return pool


# --------------------------------------------------------------------------- #
# Репозиторий: статусная машина
# --------------------------------------------------------------------------- #


async def test_create_draft_is_unique_per_incoming_dm(db):
    drafts = db.repos.dm_reply_drafts
    draft_id, created = await drafts.create_draft(
        incoming_dm_id=1, phone="+1", chat_id=555, reply_text="первый"
    )
    assert created is True

    same_id, created_again = await drafts.create_draft(
        incoming_dm_id=1, phone="+1", chat_id=555, reply_text="второй"
    )
    assert (same_id, created_again) == (draft_id, False)
    # Повторная генерация обновляет текст, пока черновик ещё draft.
    assert (await drafts.get(draft_id)).reply_text == "второй"


async def test_create_draft_never_touches_decided_draft(db):
    drafts = db.repos.dm_reply_drafts
    draft_id, _ = await drafts.create_draft(
        incoming_dm_id=1, phone="+1", chat_id=555, reply_text="одобрен"
    )
    await drafts.decide(draft_id, "approved")

    _, created = await drafts.create_draft(
        incoming_dm_id=1, phone="+1", chat_id=555, reply_text="переписан за спиной"
    )
    assert created is False
    assert (await drafts.get(draft_id)).reply_text == "одобрен"


async def test_double_concurrent_claim_claims_exactly_once(db):
    """Приёмо-сдаточный: два конкурентных клейма — ровно один победитель."""
    drafts = db.repos.dm_reply_drafts
    draft_id = await _make_draft(db, status="approved")

    claims = await asyncio.gather(drafts.claim_for_send(draft_id), drafts.claim_for_send(draft_id))

    assert sorted(claims) == [False, True]
    assert (await drafts.get(draft_id)).status == "sending"


async def test_approve_after_sent_is_idempotent(db):
    """Апрув после отправки не возвращает черновик в очередь отправки."""
    drafts = db.repos.dm_reply_drafts
    draft_id = await _make_draft(db, status="approved")
    assert await drafts.claim_for_send(draft_id)
    assert await drafts.mark_sent(draft_id, 4242)

    assert await drafts.decide(draft_id, "approved") is False

    sent = await drafts.get(draft_id)
    assert sent.status == "sent"
    assert sent.sent_message_id == 4242


async def test_rejected_draft_is_never_claimed(db):
    drafts = db.repos.dm_reply_drafts
    draft_id = await _make_draft(db, status="rejected")

    assert await drafts.claim_for_send(draft_id) is False
    assert (await drafts.get(draft_id)).status == "rejected"


async def test_release_claim_restores_approved_without_overwriting_sent(db):
    drafts = db.repos.dm_reply_drafts
    draft_id = await _make_draft(db, status="approved")

    assert await drafts.claim_for_send(draft_id)
    assert await drafts.release_claim(draft_id) is True
    assert (await drafts.get(draft_id)).status == "approved"
    # Повторный релиз без клейма — no-op.
    assert await drafts.release_claim(draft_id) is False

    # Откат не затирает более позднее состояние: sent поверх sending защищён.
    await drafts.claim_for_send(draft_id)
    await drafts.mark_sent(draft_id, 7)
    assert await drafts.release_claim(draft_id) is False
    assert (await drafts.get(draft_id)).status == "sent"


async def test_mark_sent_requires_active_claim(db):
    drafts = db.repos.dm_reply_drafts
    draft_id = await _make_draft(db, status="draft")

    assert await drafts.mark_sent(draft_id, 1) is False
    assert (await drafts.get(draft_id)).status == "draft"


async def test_decisions_skip_terminal_sending(db):
    """Решение владельца не трогает чужой активный клейм."""
    drafts = db.repos.dm_reply_drafts
    draft_id = await _make_draft(db, status="sending")

    for decision in ("approved", "rejected"):
        assert await drafts.decide(draft_id, decision) is False
    assert (await drafts.get(draft_id)).status == "sending"


# --------------------------------------------------------------------------- #
# Хендлер воркера: клейм → отправка → mark_sent
# --------------------------------------------------------------------------- #


def _patched_action(send_message: AsyncMock):
    """TelegramActionService в dm_mixin — единственная точка Telegram-транспорта."""
    svc_cls = MagicMock()
    svc_cls.return_value.send_message = send_message
    return patch("src.services.dispatcher.dm_mixin.TelegramActionService", svc_cls)


def _send_result_mock(phone: str, message_id: int | None):
    return MagicMock(phone=phone, message_id=message_id)


async def test_handler_claims_sends_once_and_marks_sent(db):
    draft_id = await _make_draft(db, status="approved")
    send_message = AsyncMock(return_value=_send_result_mock("+10000000001", 4242))
    dispatcher = TelegramCommandDispatcher(db, _mock_pool())

    with _patched_action(send_message):
        result = await dispatcher._dispatch("dm.send_reply", {"draft_id": draft_id})

    assert result == {
        "draft_id": draft_id,
        "phone": "+10000000001",
        "message_id": 4242,
        "sent": True,
    }
    send_message.assert_awaited_once()
    draft = await db.repos.dm_reply_drafts.get(draft_id)
    assert draft.status == "sent"
    assert draft.sent_message_id == 4242


async def test_handler_rerun_after_sent_does_not_send_again(db):
    """Идемпотентный повтор команды очереди после crash между mark_sent и ack."""
    draft_id = await _make_draft(db, status="approved")
    send_message = AsyncMock(return_value=_send_result_mock("+10000000001", 4242))
    dispatcher = TelegramCommandDispatcher(db, _mock_pool())

    with _patched_action(send_message):
        await dispatcher._dispatch("dm.send_reply", {"draft_id": draft_id})
        rerun = await dispatcher._dispatch("dm.send_reply", {"draft_id": draft_id})

    assert rerun == {"draft_id": draft_id, "already_sent": True}
    send_message.assert_awaited_once()


async def test_handler_concurrent_claims_send_exactly_once(db):
    """Приёмо-сдаточный: два конкурентных потребителя — отправка ровно одна."""
    draft_id = await _make_draft(db, status="approved")
    send_message = AsyncMock(return_value=_send_result_mock("+10000000001", 4242))
    dispatcher = TelegramCommandDispatcher(db, _mock_pool())

    with _patched_action(send_message):
        results = await asyncio.gather(
            dispatcher._dispatch("dm.send_reply", {"draft_id": draft_id}),
            dispatcher._dispatch("dm.send_reply", {"draft_id": draft_id}),
            return_exceptions=True,
        )

    delivered = [r for r in results if not isinstance(r, BaseException)]
    conflict = [r for r in results if isinstance(r, TelegramCommandRetryLaterError)]
    assert len(delivered) == 1
    assert len(conflict) == 1
    send_message.assert_awaited_once()
    assert (await db.repos.dm_reply_drafts.get(draft_id)).status == "sent"


async def test_handler_rejected_draft_never_sends(db):
    draft_id = await _make_draft(db, status="rejected")
    send_message = AsyncMock()
    dispatcher = TelegramCommandDispatcher(db, _mock_pool())

    with _patched_action(send_message), pytest.raises(RuntimeError, match="draft_rejected"):
        await dispatcher._dispatch("dm.send_reply", {"draft_id": draft_id})

    send_message.assert_not_awaited()
    assert (await db.repos.dm_reply_drafts.get(draft_id)).status == "rejected"


async def test_handler_claim_conflict_requeues_not_success(db):
    """Чужой живой клейм — отложенный ретрай, не благополучное завершение."""
    draft_id = await _make_draft(db, status="sending")
    send_message = AsyncMock()
    dispatcher = TelegramCommandDispatcher(db, _mock_pool())

    with (
        _patched_action(send_message),
        pytest.raises(TelegramCommandRetryLaterError, match="claim"),
    ):
        await dispatcher._dispatch("dm.send_reply", {"draft_id": draft_id})

    send_message.assert_not_awaited()
    assert (await db.repos.dm_reply_drafts.get(draft_id)).status == "sending"


async def test_reset_sending_on_startup_releases_abandoned_claim(db):
    """Lease-половина паттерна (#1036): сирота из убитого воркера возвращается
    в approved; живая свежая лиза и sent не трогаются."""
    drafts = db.repos.dm_reply_drafts
    abandoned = await _make_draft(db, status="sending", incoming_dm_id=11)
    live_claim = await _make_draft(db, status="sending", incoming_dm_id=12)
    # Тот же SQL-формат updated_at, что пишет claim (datetime('now')).
    await db.execute_write(
        "UPDATE dm_reply_drafts SET updated_at = datetime('now', '-15 minutes') WHERE id = ?",
        (abandoned,),
    )
    delivered = await _make_draft(db, status="sending", incoming_dm_id=13)
    await drafts.mark_sent(delivered, 5)
    await db.execute_write(
        "UPDATE dm_reply_drafts SET updated_at = datetime('now', '-15 minutes') WHERE id = ?",
        (delivered,),
    )

    released = await drafts.reset_sending_on_startup()

    assert released == 1
    assert (await drafts.get(abandoned)).status == "approved"
    assert (await drafts.get(live_claim)).status == "sending"
    assert (await drafts.get(delivered)).status == "sent"


async def test_handler_send_failure_releases_claim(db):
    """Неудачная отправка возвращает черновик в approved, клейм не течёт."""
    draft_id = await _make_draft(db, status="approved")
    send_message = AsyncMock(side_effect=RuntimeError("network down"))
    dispatcher = TelegramCommandDispatcher(db, _mock_pool())

    with _patched_action(send_message), pytest.raises(RuntimeError, match="network down"):
        await dispatcher._dispatch("dm.send_reply", {"draft_id": draft_id})

    assert (await db.repos.dm_reply_drafts.get(draft_id)).status == "approved"


async def test_handler_missing_draft_raises(db):
    dispatcher = TelegramCommandDispatcher(db, _mock_pool())
    with pytest.raises(RuntimeError, match="draft_not_found"):
        await dispatcher._dispatch("dm.send_reply", {"draft_id": 999})


# --------------------------------------------------------------------------- #
# CLI: только очередь, никакой прямой отправки
# --------------------------------------------------------------------------- #


def _reopen(db):
    """Переоткрыть закрытый fixture-инстанс для инспекции (паттерн handoff-тестов)."""
    if db._connection.db is None:
        asyncio.run(db.initialize())


def _run_dm_impl(coro_factory, cli_db, capsys):
    """impl закрывает БД в finally — каждый вызов начинает с переоткрытия."""
    _reopen(cli_db)
    config = AppConfig()

    async def fake_init_db(_):
        return config, cli_db

    with patch("src.cli.commands.dm.runtime.init_db", side_effect=fake_init_db):
        dm_cli.run_async(coro_factory())
    return capsys.readouterr().out


def _commands(db):
    _reopen(db)
    return asyncio.run(db.repos.telegram_commands.list_commands(limit=10))


def test_cli_send_enqueues_dm_send_reply(cli_db, capsys):
    draft_id = asyncio.run(_make_draft(cli_db, status="approved"))

    out = _run_dm_impl(lambda: dm_cli.send_impl("cfg", draft_id=draft_id), cli_db, capsys)

    assert f"#{draft_id}" in out
    assert [(c.command_type, c.payload, c.status) for c in _commands(cli_db)] == [
        ("dm.send_reply", {"draft_id": draft_id}, TelegramCommandStatus.PENDING)
    ]


def test_cli_send_deduplicates_pending_command(cli_db, capsys):
    draft_id = asyncio.run(_make_draft(cli_db, status="approved"))

    _run_dm_impl(lambda: dm_cli.send_impl("cfg", draft_id=draft_id), cli_db, capsys)
    _run_dm_impl(lambda: dm_cli.send_impl("cfg", draft_id=draft_id), cli_db, capsys)

    assert len(_commands(cli_db)) == 1


def test_cli_send_guards_statuses(cli_db, capsys):
    draft_id = asyncio.run(_make_draft(cli_db, status="rejected"))

    # Отклонённый: не approved — команда не ставится (exit 1).
    with pytest.raises(typer.Exit):
        _run_dm_impl(lambda: dm_cli.send_impl("cfg", draft_id=draft_id), cli_db, capsys)
    out = capsys.readouterr().out
    assert "нужен апрув" in out
    assert _commands(cli_db) == []

    # Отправленный: идемпотентный повтор без второй команды.
    _reopen(cli_db)
    asyncio.run(cli_db.repos.dm_reply_drafts.decide(draft_id, "approved"))
    _run_dm_impl(lambda: dm_cli.send_impl("cfg", draft_id=draft_id), cli_db, capsys)
    _reopen(cli_db)
    asyncio.run(cli_db.repos.dm_reply_drafts.claim_for_send(draft_id))
    asyncio.run(cli_db.repos.dm_reply_drafts.mark_sent(draft_id, 11))

    out = _run_dm_impl(lambda: dm_cli.send_impl("cfg", draft_id=draft_id), cli_db, capsys)
    assert "уже отправлен" in out
    assert len(_commands(cli_db)) == 1


def test_cli_approve_is_idempotent_report(cli_db, capsys):
    draft_id = asyncio.run(_make_draft(cli_db))

    out = _run_dm_impl(
        lambda: dm_cli.decide_impl("cfg", draft_id=draft_id, decision="approved"),
        cli_db,
        capsys,
    )
    assert "draft → approved" in out

    out = _run_dm_impl(
        lambda: dm_cli.decide_impl("cfg", draft_id=draft_id, decision="approved"),
        cli_db,
        capsys,
    )
    assert "остался в статусе 'approved'" in out


def test_cli_list_prints_drafts(cli_db, capsys):
    asyncio.run(_make_draft(cli_db))

    out = _run_dm_impl(lambda: dm_cli.list_impl("cfg", status=None, limit=100), cli_db, capsys)
    assert "draft" in out
