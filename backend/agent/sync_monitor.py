"""Documentation sync monitor — called by the scheduler to detect repo changes."""

from __future__ import annotations

import json
from pathlib import Path

from backend.config import settings
from backend.logger import get_logger
from backend.memory import store

log = get_logger(__name__)


def sync_check() -> None:
    """
    Called periodically by APScheduler.
    Checks if the active job's repo has new commits; if so, regenerates affected doc sections.
    """
    job = store.get_active_job()
    if not job or job.get("status") != "done":
        return

    job_id: int = job["id"]
    repo_url: str = job["repo_url"]
    last_sha: str = job.get("commit_sha", "")
    workspace_dir = Path(settings.WORKSPACE_DIR)
    repo_dir = str(workspace_dir / _repo_name(repo_url))

    from backend.agent import repo as repo_mod

    log.debug("Sync check for job %d (last SHA: %s)", job_id, last_sha[:8] if last_sha else "none")
    new_sha = repo_mod.poll_for_changes(repo_url, repo_dir, last_sha)
    if new_sha is None:
        return  # No changes

    log.info("[sync] New commit detected: %s — re-generating affected docs", new_sha[:8])

    changed_files = repo_mod.get_changed_files(repo_dir, last_sha, new_sha)
    log.info("[sync] Changed files (%d): %s", len(changed_files), ", ".join(changed_files[:10]))

    # Pull latest
    repo_mod.clone_or_update(repo_url, repo_dir)

    # Map changed files to affected doc sections
    affected = _map_to_sections(changed_files)
    log.info("[sync] Affected sections: %s", ", ".join(affected) if affected else "none")

    # Re-generate only the affected parts and re-render PDFs
    _regenerate(job_id, repo_dir, affected, new_sha)

    # Update job with new SHA
    store.update_job_commit(job_id, new_sha)

    store.set_checkpoint(job_id, "sync_completed", "done", {
        "new_sha": new_sha,
        "changed_files": changed_files[:20],
        "affected_sections": list(affected),
    })
    log.info("[sync] Documentation updated to commit %s", new_sha[:8])


def _repo_name(repo_url: str) -> str:
    """Extract a safe directory name from a repo URL."""
    return repo_url.rstrip("/").split("/")[-1].replace(".git", "")


def _map_to_sections(changed_files: list[str]) -> set[str]:
    """Map changed file paths to affected documentation sections."""
    affected: set[str] = set()
    for f in changed_files:
        fl = f.lower()
        if fl.startswith("src/") or fl.endswith(".py") or fl.endswith(".js") or fl.endswith(".ts"):
            affected.add("dev_guide")
        if "readme" in fl or fl.startswith("docs/"):
            affected.add("user_guide")
            affected.add("dev_guide")
        if any(kw in fl for kw in ["config", ".env", "settings"]):
            affected.add("dev_guide")
        if fl.startswith("tests/") or fl.startswith("test/") or "test_" in fl:
            affected.add("dev_guide")
    return affected


def _regenerate(job_id: int, repo_dir: str, sections: set[str], new_sha: str) -> None:
    """Re-run doc generation and PDF rendering for the affected sections."""
    from backend.agent import detector, doc_generator, pdf_renderer
    from backend.config import settings as cfg
    output_dir = Path(cfg.OUTPUT_DIR)

    stack_profile = detector.detect(repo_dir)
    checkpoints = store.get_checkpoints(job_id)

    def _render(html: str, out_path: str, title: str, doc_type: str) -> None:
        """Save record (incrementing version) then render with correct metadata."""
        version = store.save_document(job_id, doc_type, out_path)
        doc_records = store.get_documents(job_id)
        raw_ts = next(
            (d["generated_at"] for d in doc_records if d["doc_type"] == doc_type),
            None,
        )
        generated_at = store.fmt_generated_at(raw_ts)
        pdf_renderer.render_pdf(html, out_path, title=title,
                                generated_at=generated_at, version=version)

    if "dev_guide" in sections:
        log.info("[sync] Regenerating Developer Guide (SHA %s)", new_sha[:8])
        html = doc_generator.generate_dev_guide(job_id, stack_profile, checkpoints, repo_dir)
        out_path = str(output_dir / f"{job_id}_dev_guide.pdf")
        _render(html, out_path, "Developer Guide", "dev_guide")
        log.info("[sync] Developer Guide PDF updated: %s", out_path)

    if "user_guide" in sections:
        ui_map: dict = {"routes": []}
        cp_gui = store.get_checkpoint(job_id, "gui_inspected")
        if cp_gui and isinstance(cp_gui.get("evidence"), dict):
            routes = [{"path": r, "title": "", "description": ""} for r in cp_gui["evidence"].get("routes", [])]
            ui_map = {"routes": routes, "base_url": ""}

        log.info("[sync] Regenerating End User Guide (SHA %s)", new_sha[:8])
        html = doc_generator.generate_user_guide(job_id, stack_profile, ui_map, checkpoints, repo_dir)
        out_path = str(output_dir / f"{job_id}_user_guide.pdf")
        _render(html, out_path, "End User Guide", "user_guide")
        log.info("[sync] End User Guide PDF updated: %s", out_path)
