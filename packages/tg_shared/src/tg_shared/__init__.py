"""tg-shared — shared Telegram core for tg-agent (factory) and tg-messenger.

Scaffold (#1420, epic #1416 stage 1.1): the account model, the storage
Protocol and the default JSON store only. Module migration happens in wave A
(#1421) and wave B (#1422). The package works without the factory at all.
"""

from .json_store import JsonFileStore
from .models import SharedAccount
from .storage import SharedAccountStore

__version__ = "0.1.0"

__all__ = ["JsonFileStore", "SharedAccount", "SharedAccountStore", "__version__"]
