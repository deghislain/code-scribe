"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.logger import get_logger
from backend.memory import store
from backend.scheduler import start_scheduler, stop_scheduler
from backend.agent.orchestrator import cancel_running_jobs
from backend.api.routes import router as rest_router
from backend.api.websocket import router as ws_router

log = get_logger(__name__)

_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


_MAX_RUNNING_SECONDS = 7200  # 2 hours — jobs stuck longer than this are marked blocked


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    log.info("Code-Scribe starting up")
    store.init_db()

    # Resume any active job that didn't finish, or mark it blocked if it has
    # been "running" for an unreasonably long time (server crashed mid-job).
    import asyncio
    from datetime import datetime, timezone

    job = store.get_active_job()
    if job and job.get("status") == "running":
        updated_at_str = job.get("updated_at", "")
        stale = False
        try:
            updated_at = datetime.strptime(updated_at_str, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
            age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
            if age_seconds > _MAX_RUNNING_SECONDS:
                stale = True
        except (ValueError, TypeError):
            pass

        if stale:
            log.warning(
                "Job %d has been running for >%ds — marking blocked", job["id"], _MAX_RUNNING_SECONDS
            )
            store.update_job_status(job["id"], "blocked")
        else:
            log.info("Resuming interrupted job %d", job["id"])
            from backend.agent.orchestrator import resume_job
            asyncio.create_task(asyncio.to_thread(resume_job, job["id"]))

    start_scheduler()
    log.info("Code-Scribe ready")
    yield
    # Shutdown
    log.info("Code-Scribe shutting down")
    cancel_running_jobs()
    stop_scheduler()


app = FastAPI(title="Code-Scribe", lifespan=lifespan)

app.include_router(rest_router)
app.include_router(ws_router)

# Serve the frontend SPA as static files at "/"
if _FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")


def start() -> None:
    """Entry point for `code-scribe` CLI command."""
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    start()
