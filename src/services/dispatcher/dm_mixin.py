"""DM reply-draft command handler (#1429).

Domain: ``dm.send_reply`` — worker-only delivery of an owner-approved reply
draft. The claim/act/commit order mirrors the moderation publish path: the
atomic DB claim (``status='sending'``) is the one gate against a double send,
the irreversible Telegram write happens only after it wins, and a failed send
releases the claim without overwriting a later state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.services.telegram_actions import TelegramActionService

if TYPE_CHECKING:
    from src.services.dispatcher._base import _DispatcherProtocol

    _Base = _DispatcherProtocol
else:
    _Base = object


class DmCommandsMixin(_Base):
    """``dm.*`` command handlers."""

    async def _handle_dm_send_reply(self, payload: dict[str, Any]) -> dict[str, Any]:
        drafts = self._db.repos.dm_reply_drafts
        draft_id = int(payload["draft_id"])
        draft = await drafts.get(draft_id)
        if draft is None:
            raise RuntimeError("draft_not_found")
        # Идемпотентный повтор команды (ретрай очереди после crash между
        # mark_sent и признанием команды): черновик уже доставлен.
        if draft.status == "sent":
            return {"draft_id": draft_id, "already_sent": True}
        if draft.status == "rejected":
            raise RuntimeError("draft_rejected")
        # Атомарный клейм: второй конкурентный потребитель получает False и
        # уходит без отправки — двойная отправка невозможна (#1429).
        if not await drafts.claim_for_send(draft_id):
            return {"draft_id": draft_id, "already_claimed": True}
        try:
            result = await TelegramActionService(self._pool).send_message(
                phone=draft.phone,
                recipient=draft.chat_id,
                text=draft.reply_text,
            )
        except BaseException:
            await drafts.release_claim(draft_id)
            raise
        marked = await drafts.mark_sent(draft_id, result.message_id)
        return {
            "draft_id": draft_id,
            "phone": result.phone,
            "message_id": result.message_id,
            "sent": marked,
        }
