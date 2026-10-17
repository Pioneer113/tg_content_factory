"""`scheduler trigger` — one-shot collection run, handed to the worker (#1568).

Since #1568 the CLI enqueues a ``scheduler.trigger_collection`` telegram_command
instead of writing collection_tasks rows itself (and no longer opens a Telegram
pool). A running worker executes it via ``SchedulerManager.trigger_background``;
with no worker the command stays PENDING until one starts. Cleanup therefore
cancels BOTH leak surfaces this test can create:
- the PENDING telegram_command it enqueued (diff of before/after ids), and
- any collection_tasks rows a live worker created while executing the command
  (diff of a before/after snapshot of pending channel-collect task ids).
Never the global `scheduler clear-pending`, which would cancel every pending
task in the queue — including ones another process may have enqueued.
"""
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

from tests.cli_real_tg_integration.conftest import (
    cancel_collection_tasks,
    snapshot_pending_collection_task_ids,
)

pytestmark = pytest.mark.real_tg_safe


def _snapshot_pending_trigger_command_ids(db_path: Path) -> tuple[set[int], bool]:
    """(PENDING ``scheduler.trigger_collection`` telegram_command ids, ok).

    Same contract as ``snapshot_pending_collection_task_ids``: ``ok=False``
    means "unknown baseline" — the caller must SKIP cleanup, never diff
    against an empty set (that would over-cancel).
    """
    try:
        # closing(): sqlite3's context manager commits but never closes (#1494).
        with closing(sqlite3.connect(db_path)) as conn:
            rows = conn.execute(
                "SELECT id FROM telegram_commands "
                "WHERE command_type = ? AND status = ?",
                ("scheduler.trigger_collection", "pending"),
            ).fetchall()
    except sqlite3.Error:
        return set(), False
    return {int(row[0]) for row in rows}, True


def _cancel_trigger_commands(db_path: Path, command_ids: set[int]) -> str | None:
    """Cancel exactly the given PENDING telegram_commands (targeted SQL update).

    There is no CLI surface for cancelling a generic telegram_command, so the
    cleanup flips the row directly, scoped by id AND status=pending — a command
    already claimed by a worker is left alone.
    """
    failures: list[str] = []
    for command_id in sorted(command_ids):
        try:
            with closing(sqlite3.connect(db_path)) as conn:
                cur = conn.execute(
                    "UPDATE telegram_commands SET status = 'cancelled' "
                    "WHERE id = ? AND status = 'pending'",
                    (command_id,),
                )
                conn.commit()
                if cur.rowcount != 1:
                    failures.append(f"command {command_id}: not pending anymore")
        except sqlite3.Error as e:
            failures.append(f"command {command_id}: {e}")
    return "; ".join(failures) if failures else None


def test_proc_scheduler_trigger_enqueues(run_cli, assert_cli_ok, cli_real_cli_env):
    leak_msg: str | None = None
    before_ids, before_ok = snapshot_pending_collection_task_ids(cli_real_cli_env.db_path)
    before_cmd_ids, before_cmd_ok = _snapshot_pending_trigger_command_ids(
        cli_real_cli_env.db_path
    )
    try:
        result = run_cli("scheduler", "trigger")
        assert_cli_ok(result)
        combined = result.stdout + result.stderr
        # The handler acks with the enqueued telegram_command id (#1568).
        assert "scheduler.trigger_collection" in combined, (
            f"unexpected `scheduler trigger` output: {combined!r}"
        )
    finally:
        # Cancel the telegram_command this run created (before/after id diff).
        # The suite runs sequentially and these live CLI tests do not run a worker,
        # so within the test window this trigger is the only enqueuer of this
        # command type. A failed read = unknown baseline → skip cleanup and leak.
        if before_cmd_ok:
            after_cmd_ids, after_cmd_ok = _snapshot_pending_trigger_command_ids(
                cli_real_cli_env.db_path
            )
            if after_cmd_ok:
                new_cmd_ids = after_cmd_ids - before_cmd_ids
                if new_cmd_ids:
                    cancel_leak = _cancel_trigger_commands(
                        cli_real_cli_env.db_path, new_cmd_ids
                    )
                    if cancel_leak is not None:
                        leak_msg = (
                            f"cleanup could not cancel test-created telegram commands "
                            f"{sorted(new_cmd_ids)}: {cancel_leak}"
                        )
            elif sys.exc_info()[0] is None:
                leak_msg = (
                    "could not re-snapshot pending telegram_commands reliably; "
                    "skipping command cleanup"
                )

        # If a live worker executed the command meanwhile, cancel the collection
        # tasks it created (same before/after id scoping as before #1568). If
        # either snapshot read failed we do NOT fall back to an empty baseline
        # (that would diff to "all pending" and over-cancel); we skip and leak.
        after_ids, after_ok = snapshot_pending_collection_task_ids(cli_real_cli_env.db_path)
        if not (before_ok and after_ok):
            if sys.exc_info()[0] is None:
                leak_msg = (
                    "could not snapshot pending collection_tasks reliably; skipping cleanup to "
                    "avoid over-cancelling tasks this test did not create"
                )
        else:
            new_ids = after_ids - before_ids
            if new_ids:
                cancel_leak = cancel_collection_tasks(cli_real_cli_env, new_ids)
                if cancel_leak is not None:
                    leak_msg = (
                        f"cleanup could not cancel test-created collection tasks "
                        f"{sorted(new_ids)}: {cancel_leak}"
                    )

        # Only raise on cleanup if the try block didn't already fail.
        if leak_msg and sys.exc_info()[0] is None:
            pytest.fail(leak_msg)
