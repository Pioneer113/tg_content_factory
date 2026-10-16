# SPDX-License-Identifier: Apache-2.0
# Vendored from axisrow/telegram-mcp, telegram_mcp/runtime.py
# (commit e224762671897c1df53deadb51be84eaaaf2cea3, 2026-10-13, Apache-2.0).
# The upstream file is a monolith; only the schema-drift pieces are ported:
#   - ``_is_schema_drift`` and the TypeNotFoundError branch of
#     ``log_and_format_error``
# Local modifications on top of the vendored code:
#   - upstream's error-code scheme not ported; the schema-drift message lives
#     on ``SchemaDriftError`` with operation and constructor context instead
#   - ``_is_schema_drift`` is a plain isinstance check: telethon is a hard
#     dependency here, upstream's try/except import guard is unnecessary
#   - added ``SchemaDriftError`` (a TypeNotFoundError subclass, so existing
#     isinstance classifiers keep working) and ``translate_schema_drift`` for
#     the transport choke points in backends.py (#1549)
"""Schema-drift detection for Telethon TypeNotFoundError (#1549)."""

from __future__ import annotations

from telethon.errors import TypeNotFoundError


def _is_schema_drift(error: BaseException) -> bool:
    """True for TypeNotFoundError — the installed TL schema is older than what the server sends."""
    return isinstance(error, TypeNotFoundError)


class SchemaDriftError(TypeNotFoundError):
    """TypeNotFoundError re-raised with a self-describing message (#1549).

    A subclass on purpose: every existing classifier keeps working — the
    collector's ``unknown_tl_type_note`` warnings and broad ``except``
    handlers included — only the rendered text stops reading as
    "entity not found".
    """

    def __init__(self, original: BaseException, operation: str = "") -> None:
        self.invalid_constructor_id = getattr(original, "invalid_constructor_id", 0)
        self.remaining = getattr(original, "remaining", b"")
        ctor = self.invalid_constructor_id
        ctor_note = f" (constructor 0x{ctor:08x})" if isinstance(ctor, int) else ""
        op_note = f" during {operation}" if operation else ""
        # Exception.__init__ directly: TypeNotFoundError.__init__ would rebuild
        # the misleading "Could not find a matching Constructor ID" wording.
        Exception.__init__(
            self,
            f"MTProto schema mismatch{op_note}: the installed Telethon does not know a "
            f"Telegram object the server sent{ctor_note}. This is NOT a missing user "
            "or chat — the data arrived, parsing it failed. Upgrade Telethon; if it is "
            "already the latest release, its TL schema is behind the current layer.",
        )


def translate_schema_drift(exc: BaseException, operation: str = "") -> BaseException:
    """Return ``exc`` unchanged, or a SchemaDriftError for a TypeNotFoundError (#1549)."""
    if not _is_schema_drift(exc) or isinstance(exc, SchemaDriftError):
        return exc
    return SchemaDriftError(exc, operation=operation)
