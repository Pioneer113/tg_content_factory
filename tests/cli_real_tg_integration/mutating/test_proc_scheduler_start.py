"""`scheduler start` — setting write + worker reconcile enqueue, Popen.

Since #1569 the command no longer runs a foreground SchedulerManager on its
own pool (that collected in parallel with the daemon for the same flood-wait
budget): it writes ``scheduler_autostart=1``, enqueues ``scheduler.reconcile``
and exits. Still categorized as mutating — it flips the autostart setting and
leaves a command row for the running worker to pick up.
"""
import pytest

pytestmark = pytest.mark.real_tg_safe


@pytest.mark.timeout(60)
def test_proc_scheduler_start_writes_setting_and_exits(run_cli_popen, cli_real_cli_env):
    proc = run_cli_popen(
        "scheduler",
        "start",
        capture_stdout=True,
        extra_env={"PYTHONUNBUFFERED": "1"},
    )
    stdout_text, stderr_text = proc.communicate(timeout=30)

    if proc.returncode != 0:
        pytest.fail(
            f"`scheduler start` exited with {proc.returncode}; "
            f"stderr tail: {(stderr_text or '')[-500:]!r}"
        )
    if "Scheduler autostart enabled" not in stdout_text:
        pytest.fail(
            "`scheduler start` did not confirm the autostart write; "
            f"stdout tail: {stdout_text[-500:]!r}; stderr tail: {(stderr_text or '')[-500:]!r}"
        )
