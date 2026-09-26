"""Tests for backend/main.py — lifespan and app startup."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient


class TestLifespan:
    def test_app_starts_and_stops_without_error(self):
        """The full lifespan (startup + shutdown) runs without raising."""
        from backend.main import app
        with patch("backend.main.start_scheduler") as mock_start, \
             patch("backend.main.stop_scheduler") as mock_stop:
            with TestClient(app):
                mock_start.assert_called_once()
            mock_stop.assert_called_once()

    def test_lifespan_resumes_running_job(self, monkeypatch):
        """If a job is in 'running' state at startup, resume_job is scheduled."""
        from backend.main import app
        from backend.memory import store

        # Create a job and mark it running
        job_id = store.upsert_job("https://github.com/test/resume", commit_sha="abc")
        store.update_job_status(job_id, "running")

        scheduled = []

        def fake_create_task(coro):
            scheduled.append(coro)
            # close the coroutine to prevent ResourceWarning
            coro.close()
            return MagicMock()

        # asyncio is imported *locally* inside the lifespan `if` block — patch it
        # at the asyncio module level (create_task is called on the asyncio module itself).
        with patch("backend.main.start_scheduler"), \
             patch("backend.main.stop_scheduler"), \
             patch("asyncio.create_task", side_effect=fake_create_task):
            with TestClient(app):
                pass

        assert len(scheduled) == 1

    def test_lifespan_does_not_resume_done_job(self, monkeypatch):
        """A job with status 'done' should NOT trigger resume_job."""
        from backend.main import app
        from backend.memory import store

        job_id = store.upsert_job("https://github.com/test/done_job", commit_sha="abc")
        store.update_job_status(job_id, "done")

        scheduled = []

        def fake_create_task(coro):
            scheduled.append(coro)
            coro.close()
            return MagicMock()

        with patch("backend.main.start_scheduler"), \
             patch("backend.main.stop_scheduler"), \
             patch("asyncio.create_task", side_effect=fake_create_task):
            with TestClient(app):
                pass

        assert len(scheduled) == 0


class TestStartFunction:
    def test_start_calls_uvicorn_run(self):
        """The start() entry point calls uvicorn.run with correct args."""
        from backend.main import start
        with patch("backend.main.uvicorn.run") as mock_run:
            start()
        mock_run.assert_called_once_with(
            "backend.main:app", host="0.0.0.0", port=8000, reload=False
        )
