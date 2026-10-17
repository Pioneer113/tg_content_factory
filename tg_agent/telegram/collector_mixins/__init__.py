"""Collector composition mixins (#1137)."""

from tg_agent.telegram.collector_mixins.cancellation import CancellationMixin
from tg_agent.telegram.collector_mixins.collection import CollectionMixin
from tg_agent.telegram.collector_mixins.stats import StatsMixin
from tg_agent.telegram.collector_mixins.stream import StreamMixin

__all__ = [
    "CancellationMixin",
    "CollectionMixin",
    "StatsMixin",
    "StreamMixin",
]
