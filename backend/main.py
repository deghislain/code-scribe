"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.logger import get_logger
from backend.memory import store
from backend.scheduler import start_scheduler, stop_scheduler
from backend.agent.orchestrator import cancel_running_jobs
from backend.api.routes import router as rest_router
from backend.api.websocket import router as ws_router, init_loop

log = get_logger(__name__)

_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


_MAX_RUNNING_SECONDS = 7200  # 2 hours — jobs stuck longer than this are marked blocked


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    log.info("Code-Scribe starting up")
    store.init_db()
    init_loop()  # capture the running event loop for worker-thread log emission

    # Resume any active job that didn't finish, or mark it blocked if it has
    # been "running" for an unreasonably long time (server crashed mid-job).
    import asyncio
    from datetime import datetime, timezone

    job = store.get_active_job()
    if job and job.get("status") in ("running", "blocked"):
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
                "Job %d has been stuck for >%ds — leaving as blocked", job["id"], _MAX_RUNNING_SECONDS
            )
        else:
            # Resume: covers both "running" (clean restart) and "blocked" (container
            # was killed mid-job by Code Engine scale-to-zero — job has valid
            # checkpoints and can pick up where it left off).
            log.info("Resuming interrupted job %d (was: %s)", job["id"], job.get("status"))
            store.update_job_status(job["id"], "running")
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


@app.get("/", include_in_schema=False)
async def serve_index(request: Request) -> FileResponse:
    """Serve index.html with no-cache headers so browsers always revalidate."""
    return FileResponse(
        str(_FRONTEND_DIR / "index.html"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# Serve remaining static assets (JS, CSS, images) with normal caching
if _FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")


def start() -> None:
    """Entry point for `code-scribe` CLI command."""
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    start()
