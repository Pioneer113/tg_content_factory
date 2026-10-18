"""Process-level exit-code regressions for both supported CLI entrypoints (#1587)."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("entrypoint", ["module", "console"])
@pytest.mark.parametrize("code", [0, 1, 7])
def test_entrypoint_preserves_typer_exit(entrypoint, code):
    result = subprocess.run(
        _command(entrypoint, "finish", str(code)), check=False, capture_output=True, text=True, timeout=30,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == code, result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX SIGINT process semantics")
@pytest.mark.parametrize("entrypoint", ["module", "console"])
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
