"""Tests for backend/scheduler.py."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


class TestGetScheduler:
    def test_returns_scheduler_instance(self):
        from backend.scheduler import get_scheduler
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        sched = get_scheduler()
        assert isinstance(sched, AsyncIOScheduler)


class TestStartScheduler:
    def test_adds_job_and_starts_if_not_running(self, monkeypatch):
        from backend import scheduler

        mock_sched = MagicMock()
        mock_sched.running = False
        monkeypatch.setattr(scheduler, "_scheduler", mock_sched)

        with patch("backend.scheduler.sync_check", create=True):
            scheduler.start_scheduler()

        mock_sched.add_job.assert_called_once()
        # Verify the job id
        call_kwargs = mock_sched.add_job.call_args[1]
        assert call_kwargs["id"] == "sync_check"
        assert call_kwargs["replace_existing"] is True
        mock_sched.start.assert_called_once()

    def test_does_not_restart_if_already_running(self, monkeypatch):
        from backend import scheduler

        mock_sched = MagicMock()
        mock_sched.running = True
        monkeypatch.setattr(scheduler, "_scheduler", mock_sched)

        with patch("backend.scheduler.sync_check", create=True):
            scheduler.start_scheduler()

        mock_sched.add_job.assert_called_once()  # job is still added/replaced
        mock_sched.start.assert_not_called()  # but start is NOT called


class TestStopScheduler:
    def test_shuts_down_when_running(self, monkeypatch):
        from backend import scheduler

        mock_sched = MagicMock()
        mock_sched.running = True
        monkeypatch.setattr(scheduler, "_scheduler", mock_sched)

        scheduler.stop_scheduler()

        mock_sched.shutdown.assert_called_once_with(wait=False)

    def test_does_nothing_when_not_running(self, monkeypatch):
        from backend import scheduler

        mock_sched = MagicMock()
        mock_sched.running = False
        monkeypatch.setattr(scheduler, "_scheduler", mock_sched)

        scheduler.stop_scheduler()

        mock_sched.shutdown.assert_not_called()
