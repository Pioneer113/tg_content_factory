"""Storage contract of the shared package (#1420).

The explicit formalization of ``tg_agent/database/live_accounts.py``: the
shared layer needs exactly three things from storage — account data, the
``flood_wait_until`` write, and a string key-value for flood-layer
persistence (the resolve-backoff payload, consumed in wave B #1422). The
contract supports two configurations: the messenger with its own simple store
(``JsonFileStore``) and the factory with its ``Database``.

Method names mirror the factory facade's existing methods, so the factory
Database satisfies this Protocol structurally, without an adapter — the #1420
acceptance point of replacing ``db: Any`` with an explicit type.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .models import SharedAccount


@runtime_checkable
class SharedAccountStore(Protocol):
    """What the shared layer needs from a storage backend.

    Reads of account data carry the whole record (including
    ``flood_wait_until``); ``update_account_flood`` is the write side, a no-op
    for an unknown phone (mirrors the factory's ``UPDATE ... WHERE phone``).
    The key-value pair holds opaque string payloads — flood-layer state such
    as the resolve-backoff JSON; the package owns its keys and serialization.
    """

    async def get_accounts(self, active_only: bool = False) -> list[SharedAccount]:
        """Return known accounts; decryptability semantics belong to the implementor."""
        ...

    async def update_account_flood(self, phone: str, until: datetime | None) -> None:
        """Persist the FLOOD_WAIT deadline for one account (``None`` clears it)."""
        ...

    async def get_setting(self, key: str) -> str | None:
        """Return the value stored under ``key`` or ``None``."""
        ...

    async def set_setting(self, key: str, value: str) -> None:
        """Store ``value`` under ``key``, overwriting any previous value."""
        ...
