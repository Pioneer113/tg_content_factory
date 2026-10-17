"""Scheduler and collection-queue command handlers (#1047).

Domains: ``scheduler.*`` (reconcile / warm trigger / collection trigger) and
``collection.*`` (pause / resume / cancel task / clear pending) —
process-control commands that don't touch Telegram.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.services.dispatcher._base import _DispatcherProtocol

    _Base = _DispatcherProtocol
else:
    _Base = object


class SchedulerCommandsMixin(_Base):
    """``scheduler.*`` and ``collection.*`` command handlers."""

    async def _handle_scheduler_reconcile(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._scheduler is None:
            raise RuntimeError("scheduler_unavailable")
        autostart = await self._db.get_setting("scheduler_autostart")
        desired_running = autostart == "1"
        if not desired_running:
            await self._scheduler.stop()
            await self._scheduler.load_settings()
            return {"running": False}
        if self._scheduler.is_running:
            await self._scheduler.stop()
        await self._scheduler.load_settings()
        await self._scheduler.start()
        return {"running": True, "interval_minutes": self._scheduler.interval_minutes}

    async def _handle_scheduler_trigger_warm(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._scheduler is None:
            raise RuntimeError("scheduler_unavailable")
        await self._scheduler.trigger_warm_background()
        return {"started": True}

    async def _handle_scheduler_trigger_collection(self, payload: dict[str, Any]) -> dict[str, Any]:
        """One-shot enqueue of all channels on the live worker (#1568).

        ``started=False`` means a background run was already in flight — the
        command reports that instead of claiming success.
        """
        if self._scheduler is None:
            raise RuntimeError("scheduler_unavailable")
        started = await self._scheduler.trigger_background()
        return {"started": started}

    async def _handle_collection_cancel_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Interrupt a live collection task, not just flip its DB status (#1568).

        With the worker's own ``CollectionQueue`` this sets the in-flight
        ``cancel_event`` — the collector stops at the next check instead of
        finishing the whole channel. Without a live queue the DB flip is the
        best a caller can do (the row is never picked up in that state).
        """
        task_id = int(payload["task_id"])
        if self._collection_queue is not None:
            ok = await self._collection_queue.cancel_task(task_id)
            return {"cancelled": ok}
        ok = await self._db.repos.tasks.cancel_collection_task(task_id)
        return {"cancelled": ok}

    async def _handle_collection_clear_pending(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Drain the live worker's in-memory queue, not just the DB rows (#1568).

        ``CollectionQueue.clear_pending_tasks`` deletes the PENDING rows AND the
        in-memory ``_queue`` / flood-timer requeues / ``_known_task_ids``; the
        DB-only fallback covers the no-queue caller.
        """
        if self._collection_queue is not None:
            deleted = await self._collection_queue.clear_pending_tasks()
            return {"deleted": deleted}
        deleted = await self._db.repos.tasks.delete_pending_channel_tasks()
        return {"deleted": deleted}

    async def _handle_collection_pause(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self._db.set_setting("collection_queue_paused", "1")
        if self._collection_queue is not None:
            self._collection_queue.pause()
        return {"paused": True}

    async def _handle_collection_resume(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self._db.set_setting("collection_queue_paused", "0")
        if self._collection_queue is not None:
            self._collection_queue.resume()
        return {"paused": False}
