"""The package's own account type (#1420).

Deliberately NOT the factory's ``tg_agent.models.Account``: the package must
work without the factory, so it carries its own minimal record and never
imports factory models. The factory maps its richer Account onto this shape
when it becomes a consumer (#1424).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SharedAccount(BaseModel):
    """Minimal account data the shared layer needs: phone, session, flags.

    ``flood_wait_until`` is the FLOOD_WAIT deadline until which a client for
    this account must not be used (read side of the storage contract).
    """

    phone: str
    session_string: str
    is_active: bool = True
    flood_wait_until: datetime | None = None
