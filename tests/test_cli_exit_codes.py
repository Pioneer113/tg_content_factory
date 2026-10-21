"""Process-level exit-code regressions for both supported CLI entrypoints (#1587)."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.helpers import editable_install_staleness

# A stale editable install (#1649) keeps the dist's console_scripts pointing at
# the pre-#1582 ``src.main:main``, so every console variant dies with
# ModuleNotFoundError: No module named 'src'. Skip those with the fix in the
# reason; the module variant imports tg_agent from the workspace and still runs.
_skip_stale_console = pytest.mark.skipif(
    editable_install_staleness() is not None,
    reason="stale editable install: console script points at the pre-#1582 src layout; fix: pip install -e '.[dev]'",
)

# Substitute only the command body: parsing, Typer's non-standalone exception
# handling, main(), and the production entrypoint all run in the child process.
_SCRIPT = textwrap.dedent(
    """
    import asyncio
    import importlib
    import importlib.metadata
    import runpy
    import sys

    import typer

    cli = importlib.import_module("tg_agent.cli.main")
    app = typer.Typer()

    @app.command()
    def finish(code: int):
        raise typer.Exit(code)

    @app.command()
    def wait():
        async def pending():
            print("READY", flush=True)
            await asyncio.Event().wait()
        asyncio.run(pending())

    @app.command()
    def confirm():
        # Mirrors the [y/N] gates (filter purge, dialogs leave, ...): a blocking
        # input() that hits EOF when an agent runs the CLI with a closed stdin.
        input("Continue? [y/N] ")

    cli.app = app
    entrypoint = sys.argv.pop(1)
    if entrypoint == "module":
        runpy.run_module("tg_agent.main", run_name="__main__")
    else:
        entry = next(
            ep for ep in importlib.metadata.distribution("tg-agent").entry_points
            if ep.group == "console_scripts" and ep.name == "tg-agent"
        )
        sys.exit(entry.load()())
    """
)


def _command(entrypoint: str, *args: str) -> list[str]:
    return [sys.executable, "-c", _SCRIPT, entrypoint, *args]


@pytest.mark.parametrize("entrypoint", ["module", pytest.param("console", marks=_skip_stale_console)])
@pytest.mark.parametrize("code", [0, 1, 7])
def test_entrypoint_preserves_typer_exit(entrypoint, code):
    result = subprocess.run(
        _command(entrypoint, "finish", str(code)), check=False, capture_output=True, text=True, timeout=30,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == code, result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX SIGINT process semantics")
@pytest.mark.parametrize("entrypoint", ["module", pytest.param("console", marks=_skip_stale_console)])
def test_entrypoint_interrupted_wait_exits_nonzero(entrypoint):
    # communicate's timeout bounds startup as well as execution. A timer in the
    # child sends SIGINT only after the wait coroutine has actually started.
    script = _SCRIPT.replace(
        'print("READY", flush=True)',
        'asyncio.get_running_loop().call_later(0.05, os.kill, os.getpid(), signal.SIGINT)',
    )
    script = "import os, signal\n" + script
    result = subprocess.run(
        [sys.executable, "-c", script, entrypoint, "wait"],
        check=False, capture_output=True, text=True, timeout=30,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode in (1, 130), result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("entrypoint", ["module", pytest.param("console", marks=_skip_stale_console)])
def test_entrypoint_confirm_eof_exits_cleanly(entrypoint):
    # Issue #1590: EOF at a confirmation prompt (closed stdin, no TTY) used to
    # escape as Typer's own Abort — a RuntimeError main() did not catch — and
    # print a traceback. It must be a clean non-zero refusal instead.
    result = subprocess.run(
        _command(entrypoint, "confirm"),
        check=False, capture_output=True, text=True, timeout=30,
        stdin=subprocess.DEVNULL,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == 1, result.stderr
    assert "Traceback" not in result.stderr
    assert "EOFError" not in result.stderr
    assert "Aborted" in result.stderr
