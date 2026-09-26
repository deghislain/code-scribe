"""APScheduler background polling setup."""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.config import settings

_scheduler = AsyncIOScheduler()


def get_scheduler() -> AsyncIOScheduler:
    return _scheduler


def start_scheduler() -> None:
    """Configure and start the background scheduler."""
    from backend.agent.sync_monitor import sync_check  # lazy import

    _scheduler.add_job(
        sync_check,
        trigger="interval",
        seconds=settings.POLL_INTERVAL_SECONDS,
        id="sync_check",
        replace_existing=True,
        max_instances=1,
    )
    if not _scheduler.running:
        _scheduler.start()


def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
