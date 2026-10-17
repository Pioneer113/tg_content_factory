"""CLI scheduler sync-job-state tests (survivor of the test-hygiene audit).

The former per-CLI-module "coverage" classes here ran ``run(args)`` against
fully mocked dependencies with zero assertions; control mutations that reduced
each ``run()`` adapter to a no-op left all of them green, so they verified
nothing and were removed (test-hygiene PR, 2026-10-12).
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


class TestSchedulerSyncJobState:
    async def test_sync_job_disable(self):
        from tg_agent.scheduler.service import SchedulerManager
        mgr = SchedulerManager()
        mgr._scheduler = MagicMock()
        mgr._scheduler.running = True
        mgr._scheduler.remove_job = MagicMock()
        await mgr.sync_job_state("collect", False)
        mgr._scheduler.remove_job.assert_called_once_with("collect")

    async def test_sync_job_enable_collection(self):
        from tg_agent.scheduler.service import SchedulerManager
        mgr = SchedulerManager()
        mgr._scheduler = MagicMock()
        mgr._scheduler.running = True
        mgr._job_id = "collect_all"
        mgr._current_interval_minutes = 10
        await mgr.sync_job_state("collect_all", True)
        mgr._scheduler.add_job.assert_called_once()

    async def test_sync_job_enable_photo_due(self):
        from tg_agent.scheduler.service import SchedulerManager
        mgr = SchedulerManager()
        mgr._scheduler = MagicMock()
        mgr._scheduler.running = True
        mgr._task_enqueuer = MagicMock()
        await mgr.sync_job_state("photo_due", True)
        mgr._scheduler.add_job.assert_called_once()

    async def test_sync_job_enable_photo_auto(self):
        from tg_agent.scheduler.service import SchedulerManager
        mgr = SchedulerManager()
        mgr._scheduler = MagicMock()
        mgr._scheduler.running = True
        mgr._task_enqueuer = MagicMock()
        await mgr.sync_job_state("photo_auto", True)
        mgr._scheduler.add_job.assert_called_once()

    async def test_sync_job_enable_ping_due(self):
        from src.scheduler.service import SchedulerManager
        mgr = SchedulerManager()
        mgr._scheduler = MagicMock()
        mgr._scheduler.running = True
        mgr._task_enqueuer = MagicMock()
        await mgr.sync_job_state("ping_due", True)
        mgr._scheduler.add_job.assert_called_once()

    async def test_sync_job_enable_sq_prefix(self):
        from tg_agent.scheduler.service import SchedulerManager
        mgr = SchedulerManager()
        mgr._scheduler = MagicMock()
        mgr._scheduler.running = True
        with patch.object(mgr, "sync_search_query_jobs", new_callable=AsyncMock):
            await mgr.sync_job_state("sq_1", True)
            mgr.sync_search_query_jobs.assert_called_once()

    async def test_sync_job_enable_pipeline_prefix(self):
        from tg_agent.scheduler.service import SchedulerManager
        mgr = SchedulerManager()
        mgr._scheduler = MagicMock()
        mgr._scheduler.running = True
        with patch.object(mgr, "sync_pipeline_jobs", new_callable=AsyncMock):
            await mgr.sync_job_state("pipeline_run_1", True)
            mgr.sync_pipeline_jobs.assert_called_once()

    async def test_get_job_next_run_fallback(self):
        from tg_agent.scheduler.service import SchedulerManager
        mgr = SchedulerManager()
        job = SimpleNamespace(id="test_job", next_run_time="2025-01-01")
        mgr._scheduler = MagicMock()
        mgr._scheduler.get_job = MagicMock(side_effect=Exception("boom"))
        mgr._scheduler.get_jobs = MagicMock(return_value=[job])
        result = mgr.get_job_next_run("test_job")
        assert result == "2025-01-01"

    async def test_get_all_jobs_cache(self):
        from tg_agent.scheduler.service import SchedulerManager
        mgr = SchedulerManager()
        mgr._scheduler = MagicMock()
        mgr._scheduler.get_jobs = MagicMock(return_value=[])
        mgr._jobs_cache = {"cached": "data"}
        mgr._jobs_cache_ts = time.monotonic()
        result = mgr.get_all_jobs_next_run()
        assert result == {"cached": "data"}
