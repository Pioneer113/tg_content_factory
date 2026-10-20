"""Auto-restart of the managed daemon when the checked-out src/ is newer.

The CLI edits src/ but forgets to restart the worker daemon, which then runs
stale code for hours (found live 2026-10-02: daemon from 01:00 kept crashing
in the notification-snapshot path on code fixed later that morning). The
decision logic lives in tg_agent/cli/daemon_autorestart.py; the PostToolUse hook
calls the scripts/ shim after every Bash tool call.

The freshness tests cover the is_stale gate in isolation; the test_main_* tests
cover the full hook decision path (daemon_pid -> working_tree_clean -> is_stale ->
restart) with the subprocess boundary faked — no real daemon is ever touched.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from tg_agent.cli import daemon_autorestart
from tg_agent.cli.daemon_autorestart import is_stale


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n", encoding="utf-8")
    # Ensure a strictly monotonic mtime even on coarse-grained filesystems.
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime + 1))


def _write_pid_file(tmp_path: Path) -> Path:
    pid_file = tmp_path / "data" / "tg_search.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text("12345\n", encoding="utf-8")
    return pid_file


def test_is_stale_when_src_newer_than_pid_file(tmp_path):
    pid_file = _write_pid_file(tmp_path)
    _touch(tmp_path / "tg_agent" / "cli" / "commands" / "scheduler.py")

    assert is_stale(pid_file, tmp_path / "tg_agent") is True


def test_not_stale_when_daemon_fresh(tmp_path):
    pid_file = _write_pid_file(tmp_path)
    _touch(tmp_path / "tg_agent" / "main.py")
    # Daemon started after the last src edit.
    st = os.stat(pid_file)
    os.utime(pid_file, (st.st_atime, st.st_mtime + 10))

    assert is_stale(pid_file, tmp_path / "tg_agent") is False


def test_not_stale_without_pid_file(tmp_path):
    _touch(tmp_path / "tg_agent" / "main.py")

    assert is_stale(tmp_path / "data" / "tg_search.pid", tmp_path / "tg_agent") is False


def test_not_stale_when_mtime_equal(tmp_path):
    pid_file = _write_pid_file(tmp_path)
    src_file = tmp_path / "tg_agent" / "main.py"
    _touch(src_file)
    src_st = os.stat(src_file)
    os.utime(pid_file, (src_st.st_atime, src_st.st_mtime))

    assert is_stale(pid_file, tmp_path / "tg_agent") is False


def test_is_stale_ignores_non_python_files(tmp_path):
    pid_file = _write_pid_file(tmp_path)
    _touch(tmp_path / "tg_agent" / "web" / "templates" / "index.html")

    assert is_stale(pid_file, tmp_path / "tg_agent") is False


def test_pid_file_mtime_is_the_daemon_start_proxy(tmp_path):
    """The staleness contract: the PID file is (re)written fresh at worker
    startup, so its mtime is the daemon build time."""
    from tg_agent.cli.process_control import register_current_process

    pid_file = tmp_path / "tg_search.pid"
    register_current_process(pid_file)

    assert int(pid_file.read_text()) == os.getpid()
    assert abs(pid_file.stat().st_mtime - time.time()) < 60


class _FakeSubprocess:
    """subprocess double for the hook path: ``ps``/``git`` canned, Popen recorded."""

    STDOUT = subprocess.STDOUT
    DEVNULL = subprocess.DEVNULL

    def __init__(self, ps_output: str, clean: bool):
        self.ps_output = ps_output
        self.clean = clean
        self.popen_calls: list[tuple[list[str], dict]] = []

    def run(self, cmd, *, capture_output, check, text, timeout, cwd=None):
        if cmd[0] == "ps":
            return subprocess.CompletedProcess(cmd, 0, stdout=self.ps_output)
        assert cmd[0] == "git"
        return subprocess.CompletedProcess(cmd, 0, stdout="" if self.clean else " M tg_agent/cli/main.py\n")

    # noqa: N802 — must mirror subprocess.Popen, main() calls it by that name
    def Popen(self, cmd, **kwargs):  # noqa: N802
        self.popen_calls.append((list(cmd), kwargs))
        return None


def _hook_env(monkeypatch, tmp_path, *, managed: bool, clean: bool, with_pid: bool = True) -> _FakeSubprocess:
    """Point main() at a fake repo root behind a fake subprocess boundary.

    The PID file holds a foreign-but-plausible pid; whether it counts as a
    managed daemon is decided by the canned ``ps`` output. The src tree is
    touched newer than the PID file (stale) unless a test bumps the PID mtime.
    """
    fake = _FakeSubprocess(
        ps_output="python -m tg_agent.main worker" if managed else "/sbin/launchd bootstrap thing",
        clean=clean,
    )
    monkeypatch.setattr(daemon_autorestart, "subprocess", fake)
    monkeypatch.setattr(daemon_autorestart, "REPO_ROOT", tmp_path)
    if with_pid:
        pid_file = tmp_path / "data" / "tg_search.pid"
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text("4242\n", encoding="utf-8")
    _touch(tmp_path / "tg_agent" / "cli" / "main.py")
    return fake


def _daemon_restarted(pid_file: Path) -> None:
    """Simulate the restarted daemon rewriting the PID file at startup."""
    st = os.stat(pid_file)
    os.utime(pid_file, (st.st_atime, st.st_mtime + 10))


def test_main_restarts_once_detached_when_all_gates_hold(monkeypatch, tmp_path):
    fake = _hook_env(monkeypatch, tmp_path, managed=True, clean=True)

    daemon_autorestart.main()

    assert len(fake.popen_calls) == 1
    cmd, kwargs = fake.popen_calls[0]
    assert cmd[1:3] == ["-m", "tg_agent.main"]
    assert "restart" in cmd
    assert kwargs["start_new_session"] is True  # detached: the hook process may exit first
    assert kwargs["cwd"] == tmp_path
    assert kwargs["stdin"] == subprocess.DEVNULL


def test_main_no_restart_for_foreign_process(monkeypatch, tmp_path):
    fake = _hook_env(monkeypatch, tmp_path, managed=False, clean=True)

    daemon_autorestart.main()

    assert fake.popen_calls == []


def test_main_no_restart_without_pid_file(monkeypatch, tmp_path):
    fake = _hook_env(monkeypatch, tmp_path, managed=True, clean=True, with_pid=False)

    daemon_autorestart.main()

    assert fake.popen_calls == []


def test_main_no_restart_when_tree_is_dirty(monkeypatch, tmp_path):
    fake = _hook_env(monkeypatch, tmp_path, managed=True, clean=False)

    daemon_autorestart.main()

    assert fake.popen_calls == []


def test_main_no_restart_when_daemon_built_after_last_edit(monkeypatch, tmp_path):
    fake = _hook_env(monkeypatch, tmp_path, managed=True, clean=True)
    _daemon_restarted(tmp_path / "data" / "tg_search.pid")

    daemon_autorestart.main()

    assert fake.popen_calls == []


def test_main_fresh_pid_after_restart_makes_next_hook_call_noop(monkeypatch, tmp_path):
    fake = _hook_env(monkeypatch, tmp_path, managed=True, clean=True)

    daemon_autorestart.main()
    _daemon_restarted(tmp_path / "data" / "tg_search.pid")  # the restarted daemon is the newest build now
    daemon_autorestart.main()

    assert len(fake.popen_calls) == 1
