"""``dm`` CLI group — черновики ответов на входящие DM (эпик #1416 этап 2.4, #1429).

Typer-группа (эпик #959): тонкие ``@command``-обёртки над ``*_impl``.
Отправка никогда не идёт из CLI напрямую — команда ``dm.send_reply``
ставится в очередь и исполняется воркером с пулом (граница из
``routes/moderation.py``). Атомарный клейм в хендлере защищает от двойной
отправки при конкурентных потребителях и ретраях очереди.
"""

from __future__ import annotations

from typing import Literal

import typer

from src.cli import runtime
from src.cli.commands.common import apply_startup, run_async
from src.services.telegram_command_service import TelegramCommandService


async def list_impl(config_path: str, *, status: str | None, limit: int) -> None:
    _, db = await runtime.init_db(config_path)
    try:
        drafts = await db.repos.dm_reply_drafts.list_drafts(status=status, limit=limit)
        if not drafts:
            print("No reply drafts found.")
            return
        fmt = "{:<5} {:<10} {:<16} {:<12} {:<11} {:<30}"
        print(fmt.format("ID", "Status", "Chat", "Sent msg", "Updated", "Reply"))
        print("-" * 95)
        for d in drafts:
            print(
                fmt.format(
                    str(d.id),
                    d.status,
                    str(d.chat_id),
                    str(d.sent_message_id if d.sent_message_id is not None else "-"),
                    (d.updated_at.strftime("%Y-%m-%d %H:%M") if d.updated_at else "-"),
                    (d.reply_text[:28] + "…" if len(d.reply_text) > 28 else d.reply_text),
                )
            )
    finally:
        await db.close()


async def create_impl(
    config_path: str,
    *,
    incoming_dm_id: int,
    text: str,
    phone: str | None,
    chat_id: int | None,
) -> None:
    _, db = await runtime.init_db(config_path)
    try:
        target_phone = phone
        target_chat_id = chat_id
        if target_phone is None or target_chat_id is None:
            journal = await db.repos.incoming_dms.get(incoming_dm_id)
            if journal is not None:
                target_phone = target_phone or journal.phone
                target_chat_id = target_chat_id or journal.chat_id
        if not target_phone or target_chat_id is None:
            print(
                f"Не найдена запись журнала incoming_dms #{incoming_dm_id} "
                "(TTL 24ч) — укажите --phone и --chat-id явно."
            )
            raise typer.Exit(1)
        draft_id, created = await db.repos.dm_reply_drafts.create_draft(
            incoming_dm_id=incoming_dm_id,
            phone=target_phone,
            chat_id=target_chat_id,
            reply_text=text,
        )
        if created:
            print(f"Черновик #{draft_id} создан (статус draft).")
        else:
            print(
                f"Черновик #{draft_id} уже существует — текст не изменён "
                "(обновление возможно только в статусе draft)."
            )
    finally:
        await db.close()


async def decide_impl(
    config_path: str, *, draft_id: int, decision: Literal["approved", "rejected"]
) -> None:
    _, db = await runtime.init_db(config_path)
    try:
        draft = await db.repos.dm_reply_drafts.get(draft_id)
        if draft is None:
            print(f"Черновик #{draft_id} не найден.")
            raise typer.Exit(1)
        changed = await db.repos.dm_reply_drafts.decide(draft_id, decision)
        if changed:
            print(f"Черновик #{draft_id}: {draft.status} → {decision}.")
        else:
            # Идемпотентный no-op: отправленный/клеймленный черновик решением
            # владельца назад не переводится — двойная отправка исключена.
            print(
                f"Черновик #{draft_id} остался в статусе '{draft.status}' "
                f"(решения не применяются к sending/sent)."
            )
    finally:
        await db.close()


async def send_impl(config_path: str, *, draft_id: int) -> None:
    """Поставить отправку одобренного черновика в очередь воркера."""
    _, db = await runtime.init_db(config_path)
    try:
        draft = await db.repos.dm_reply_drafts.get(draft_id)
        if draft is None:
            print(f"Черновик #{draft_id} не найден.")
            raise typer.Exit(1)
        if draft.status == "sent":
            print(
                f"Черновик #{draft_id} уже отправлен "
                f"(message_id={draft.sent_message_id}); повторная отправка исключена."
            )
            return
        if draft.status != "approved":
            print(
                f"Черновик #{draft_id} в статусе '{draft.status}' — "
                "перед отправкой нужен апрув: dm drafts approve."
            )
            raise typer.Exit(1)
        command_id = await TelegramCommandService(db).enqueue(
            "dm.send_reply",
            payload={"draft_id": draft_id},
            requested_by="cli:dm",
        )
        print(
            f"Команда dm.send_reply #{command_id} поставлена в очередь воркера "
            f"(черновик #{draft_id}). Статус: dm drafts list."
        )
    finally:
        await db.close()


dm_app = typer.Typer(no_args_is_help=True, help="Incoming DM reply drafts")
drafts_app = typer.Typer(no_args_is_help=True, help="Reply drafts: list, decide, queue send")
dm_app.add_typer(drafts_app, name="drafts")


@drafts_app.command("list")
def drafts_list(
    ctx: typer.Context,
    status: str | None = typer.Option(None, "--status", help="Filter by status"),
    limit: int = typer.Option(100, "--limit", help="Max rows"),
) -> None:
    """List reply drafts (fresh first)."""
    apply_startup(ctx)
    run_async(list_impl(ctx.obj.config, status=status, limit=limit))


@drafts_app.command("create")
def drafts_create(
    ctx: typer.Context,
    incoming_dm_id: int = typer.Argument(..., metavar="incoming-dm-id"),
    text: str = typer.Option(..., "--text", help="Reply text"),
    phone: str | None = typer.Option(
        None, "--phone", help="Account phone if the journal row is already pruned (24h TTL)"
    ),
    chat_id: int | None = typer.Option(
        None, "--chat-id", help="Chat id if the journal row is already pruned (24h TTL)"
    ),
) -> None:
    """Create (or re-draft, while still in draft) a reply for an incoming DM."""
    apply_startup(ctx)
    run_async(
        create_impl(
            ctx.obj.config,
            incoming_dm_id=incoming_dm_id,
            text=text,
            phone=phone,
            chat_id=chat_id,
        )
    )


@drafts_app.command("approve")
def drafts_approve(
    ctx: typer.Context,
    draft_id: int = typer.Argument(..., metavar="id"),
) -> None:
    """Approve a draft for sending (no-op once sending/sent)."""
    apply_startup(ctx)
    run_async(decide_impl(ctx.obj.config, draft_id=draft_id, decision="approved"))


@drafts_app.command("reject")
def drafts_reject(
    ctx: typer.Context,
    draft_id: int = typer.Argument(..., metavar="id"),
) -> None:
    """Reject a draft — it can never be claimed for sending."""
    apply_startup(ctx)
    run_async(decide_impl(ctx.obj.config, draft_id=draft_id, decision="rejected"))


@drafts_app.command("send")
def drafts_send(
    ctx: typer.Context,
    draft_id: int = typer.Argument(..., metavar="id"),
) -> None:
    """Queue an approved draft for the worker to send (never sends in-process)."""
    apply_startup(ctx)
    run_async(send_impl(ctx.obj.config, draft_id=draft_id))
