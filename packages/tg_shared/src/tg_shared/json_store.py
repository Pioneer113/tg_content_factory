"""Default store for the standalone single-account messenger (#1420).

One JSON file, atomic writes (tmp + replace), one asyncio lock per instance.
This gives the messenger the persistence it historically lacked — the epic
#1416 comparison table records "survives restart: messenger — no" for flood
waits. Pure storage: no telegram imports, works without the factory.

A corrupt file raises loudly (pydantic/json errors) rather than silently
resetting — a store that forgets flood deadlines is the failure mode this
package exists to prevent.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from .models import SharedAccount

_ACCOUNTS = TypeAdapter(list[SharedAccount])


class JsonFileStore:
    """File-backed `SharedAccountStore` for one account and its settings."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = asyncio.Lock()

    async def add_account(self, account: SharedAccount) -> None:
        """Seed or replace one account row (convenience beyond the Protocol)."""
        async with self._lock:
            data = self._read()
            accounts = [
                item for item in _ACCOUNTS.validate_python(data.get("accounts", [])) if item.phone != account.phone
            ]
            accounts.append(account)
            data["accounts"] = _ACCOUNTS.dump_python(accounts, mode="json")
            self._write(data)

    async def get_accounts(self, active_only: bool = False) -> list[SharedAccount]:
        async with self._lock:
            accounts = _ACCOUNTS.validate_python(self._read().get("accounts", []))
            if active_only:
                accounts = [item for item in accounts if item.is_active]
            return accounts

    async def update_account_flood(self, phone: str, until: datetime | None) -> None:
        async with self._lock:
            data = self._read()
            accounts = _ACCOUNTS.validate_python(data.get("accounts", []))
            for item in accounts:
                if item.phone == phone:
                    item.flood_wait_until = until
                    break
            else:
                return  # unknown phone: no-op, mirrors the factory's UPDATE ... WHERE phone
            data["accounts"] = _ACCOUNTS.dump_python(accounts, mode="json")
            self._write(data)

    async def get_setting(self, key: str) -> str | None:
        async with self._lock:
            return self._read().get("settings", {}).get(key)

    async def set_setting(self, key: str, value: str) -> None:
        async with self._lock:
            data = self._read()
            data.setdefault("settings", {})[key] = value
            self._write(data)

    def _read(self) -> dict[str, Any]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        return json.loads(raw)

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)
