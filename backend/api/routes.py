"""REST API routes for Code-Scribe."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.agent import chat_handler
from backend.config import settings
from backend.logger import get_logger
from backend.memory import store

log = get_logger(__name__)

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class JobRequest(BaseModel):
    repo_url: str


class ChatRequest(BaseModel):
    job_id: int
    message: str


# ---------------------------------------------------------------------------
# Lazy import of orchestrator to avoid circular imports at module load time
# ---------------------------------------------------------------------------

def _get_orchestrator():
    from backend.agent import orchestrator
    return orchestrator


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/jobs")
async def submit_job(body: JobRequest):
    """Accept a repo URL, create a fresh job record, start the orchestrator."""
    job_id = store.create_job(body.repo_url)
    store.update_job_status(job_id, "running")
    orch = _get_orchestrator()
    asyncio.create_task(asyncio.to_thread(orch.run_job, job_id))
    return {"job_id": job_id, "status": "running"}


@router.get("/jobs/current")
def get_current_job():
    """Return the most recent job."""
    job = store.get_active_job()
    if not job:
        return {"status": "idle"}
    return job


@router.get("/jobs/{job_id}/checkpoints")
def get_checkpoints(job_id: int):
    return store.get_checkpoints(job_id)


@router.get("/jobs/{job_id}/documents")
def get_documents(job_id: int):
    """Return documents for a job, filtered to only those whose PDF file exists."""
    docs = store.get_documents(job_id)
    output_dir = Path(settings.OUTPUT_DIR)
    return [
        d for d in docs
        if (output_dir / Path(d["file_path"]).name).exists()
    ]


@router.get("/documents/{filename}")
def download_document(filename: str):
    """Serve a generated PDF from the outputs directory."""
    output_dir = Path(settings.OUTPUT_DIR)
    path = output_dir / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Document not found")
    # Prevent path traversal
    try:
        path.resolve().relative_to(output_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return FileResponse(str(path), media_type="application/pdf", filename=filename)


@router.delete("/jobs/{job_id}/documents/{doc_type}")
def delete_document(job_id: int, doc_type: str):
    """
    Delete a generated document.

    Removes the record from the database and the PDF file from disk.
    Returns 404 if the document doesn't exist for this job.
    """
    docs = store.get_documents(job_id)
    match = next((d for d in docs if d["doc_type"] == doc_type), None)
    if match is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # Remove the file from disk (best-effort — don't fail if already gone)
    file_path = Path(match["file_path"])
    if file_path.exists():
        try:
            file_path.unlink()
            log.info("Deleted document file: %s", file_path)
        except OSError as exc:
            log.warning("Could not delete file %s: %s", file_path, exc)

    store.delete_document(job_id, doc_type)
    return {"deleted": True, "doc_type": doc_type}


@router.post("/chat")
def chat(body: ChatRequest):
    """Answer a user question grounded in the job's analysis."""
    job = store.get_job(body.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    response = chat_handler.answer_question(body.job_id, body.message)
    return {"response": response}
