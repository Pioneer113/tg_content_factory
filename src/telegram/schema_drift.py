# SPDX-License-Identifier: Apache-2.0
# Vendored from axisrow/telegram-mcp, telegram_mcp/runtime.py
# (commit e224762671897c1df53deadb51be84eaaaf2cea3, 2026-10-13, Apache-2.0).
# The upstream file is a monolith; only the schema-drift pieces are ported:
#   - ``_is_schema_drift`` (verbatim)
#   - the TypeNotFoundError branch of ``log_and_format_error`` (verbatim text,
#     parameterised on the caller-supplied error code)
# The rest of runtime.py (logging, error codes, alias handling) is NOT ported.
"""Schema-drift detection for Telethon TypeNotFoundError (#1549)."""

from __future__ import annotations


def _is_schema_drift(error: Exception) -> bool:
    """True for TypeNotFoundError — the installed TL schema is older than what the server sends."""
    try:
        from telethon.errors.common import TypeNotFoundError
    except Exception:  # telethon missing or moved — not this helper's problem
        return False
    return isinstance(error, TypeNotFoundError)


def schema_drift_message(error_code: str) -> str:
    """Upstream schema-drift message, parameterised on the stable error code."""
    return (
        f"MTProto schema mismatch: the installed Telethon does not know an object the "
        f"server sent. This is NOT a missing user or chat — the data arrived, "
        f"parsing it failed. Upgrade Telethon; if it is already the latest release, its "
        f"schema is behind the current layer (code: {error_code})."
    )
