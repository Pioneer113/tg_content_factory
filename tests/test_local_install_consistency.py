"""Detect a stale local editable install of tg-agent (#1649).

An editable install that predates the #1582 ``src/`` → ``tg_agent/`` rename
keeps its console_scripts pointing at ``src.main:main``, so console tests fail
with a cryptic ``ModuleNotFoundError: No module named 'src'`` on every local
run while CI (fresh install each job) stays green. This test fails once, with
the fix in the message, instead of leaving N mysteries to triage.
"""
from __future__ import annotations

from tests.helpers import editable_install_staleness


def test_editable_install_matches_workspace():
    staleness = editable_install_staleness()
    assert staleness is None, (
        f"Stale editable install: {staleness}.\n"
        "Console-entrypoint tests fail with ModuleNotFoundError: No module named 'src'.\n"
        'Fix: pip install -e ".[dev]"  (then verify: python -m pip show tg-agent)'
    )
