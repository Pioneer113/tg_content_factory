"""Backward-compatibility shim — canonical location is tg_agent/scheduler/service.py."""
from tg_agent.scheduler.service import SchedulerManager  # noqa: F401

__all__ = ["SchedulerManager"]
