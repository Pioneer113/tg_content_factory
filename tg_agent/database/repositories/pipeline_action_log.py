"""Леджер обработанных нодами пайплайна сообщений (дедуп react/forward/delete)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tg_agent.database.pool import ReadConnection

if TYPE_CHECKING:
    from tg_agent.database.facade import Database


class PipelineActionLogRepository:
    """Tracks which messages a pipeline node already acted on, so re-runs over
    the same time window do not re-react/re-forward/re-delete them (issue #471)."""

    def __init__(
        self,
        db: ReadConnection,
        *,
        database: "Database | None" = None,
    ):
        self._db = db
        self._database = database

    async def processed_message_ids(
        self,
        pipeline_id: int,
        node_id: str,
        action: str,
        since_hours: float | None = None,
        target: str | None = None,
    ) -> set[tuple[int, int]]:
        """Return already-processed ``(channel_id, message_id)`` pairs for this
        pipeline+node+action.

        The pair — not the bare message_id — is the identity: Telegram message ids
        are per-channel (both start at 1), so a pipeline pulling from several source
        channels could otherwise dedup a fresh message in channel B against an
        already-processed message with the same id in channel A.

        When ``target`` is given, rows are additionally scoped to that delivery
        destination — or to legacy rows recorded before per-target scoping existed
        (target IS NULL, treated as "delivered everywhere"). A message already
        forwarded to dialog A therefore no longer blocks a retry delivering it to
        dialog B (issue #1537).

        When ``since_hours`` is given, only rows newer than that window are loaded.
        Messages older than the fetch window can never match the current run's
        candidates anyway, so this caps memory/scan growth on long-lived pipelines
        instead of reading the whole unbounded log every run.
        """
        sql = (
            "SELECT channel_id, message_id FROM pipeline_action_log "
            "WHERE pipeline_id = ? AND node_id = ? AND action = ?"
        )
        params: list = [pipeline_id, node_id, action]
        if since_hours is not None:
            sql += " AND created_at > datetime('now', ?)"
            params.append(f"-{float(since_hours)} hours")
        if target is not None:
            sql += " AND (target IS NULL OR target = ?)"
            params.append(target)
        cur = await self._db.execute(sql, params)
        rows = await cur.fetchall()
        return {(int(r["channel_id"]), int(r["message_id"])) for r in rows}

    async def log_action(
        self,
        pipeline_id: int,
        node_id: str,
        action: str,
        channel_id: int,
        message_id: int,
        target: str | None = None,
    ) -> None:
        """Record that (pipeline, node, action) acted on a message. Idempotent.

        ``target`` scopes the record to a delivery destination (issue #1537);
        it stays NULL for actions that are once-per-message (react, delete).
        """
        assert self._database is not None, (
            "PipelineActionLogRepository.log_action requires a Database reference"
        )
        await self._database.execute_write(
            "INSERT OR IGNORE INTO pipeline_action_log "
            "(pipeline_id, node_id, action, channel_id, message_id, target) VALUES (?, ?, ?, ?, ?, ?)",
            (pipeline_id, node_id, action, channel_id, message_id, target),
        )
