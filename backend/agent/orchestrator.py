"""Top-level orchestrator state machine."""

from __future__ import annotations

import json
import traceback
from pathlib import Path

from backend.config import settings
from backend.logger import get_logger
from backend.memory import store

log = get_logger(__name__)


def _render_document(
    html: str,
    out_path: str,
    title: str,
    job_id: int,
    doc_type: str,
) -> None:
    """
    Save the document record (incrementing version), then render the PDF
    with the correct generation timestamp and version stamped on the cover.
    """
    from backend.agent import pdf_renderer

    version = store.save_document(job_id, doc_type, out_path)
    doc_record = store.get_documents(job_id)
    raw_ts = next(
        (d["generated_at"] for d in doc_record if d["doc_type"] == doc_type),
        None,
    )
    generated_at = store.fmt_generated_at(raw_ts)
    pdf_renderer.render_pdf(
        html,
        out_path,
        title=title,
        generated_at=generated_at,
        version=version,
    )


# ---------------------------------------------------------------------------
# Checkpoint names (canonical)
# ---------------------------------------------------------------------------
CP_REPO_CLONED = "repo_cloned"
CP_STACK_DETECTED = "stack_detected"
CP_DEPS_INSTALLED = "deps_installed"
CP_BUILD_DONE = "build_done"
CP_TESTS_RUN = "tests_run"
CP_APP_LAUNCHED = "app_launched"
CP_GUI_INSPECTED = "gui_inspected"
CP_USER_GUIDE = "user_guide_generated"
CP_DEV_GUIDE = "dev_guide_generated"
CP_CHAT_INJECTED = "chat_view_injected"
CP_TESTS_RERUN = "tests_rerun"
CP_MEMORY_SAVED = "memory_saved"


def _is_done(checkpoints: list[dict], name: str) -> bool:
    for cp in checkpoints:
        if cp["checkpoint_name"] == name and cp["status"] == "done":
            return True
    return False


def _repo_name(repo_url: str) -> str:
    return repo_url.rstrip("/").split("/")[-1].replace(".git", "")


def run_job(job_id: int) -> None:
    """
    Execute the full 12-step analysis workflow.
    Resumes from last completed checkpoint on restart.
    """
    job = store.get_job(job_id)
    if not job:
        log.error("Job %d not found — aborting", job_id)
        return

    repo_url: str = job["repo_url"]
    workspace_dir = Path(settings.WORKSPACE_DIR)
    output_dir = Path(settings.OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    repo_dir = str(workspace_dir / _repo_name(repo_url))
    checkpoints = store.get_checkpoints(job_id)

    log.info("Job %d starting — repo: %s", job_id, repo_url)

    # --- Step 1: Clone / update repo -----------------------------------------
    if not _is_done(checkpoints, CP_REPO_CLONED):
        log.info("[1/12] Cloning repository")
        try:
            from backend.agent import repo as repo_mod
            sha = repo_mod.clone_or_update(repo_url, repo_dir)
            store.update_job_commit(job_id, sha)
            store.set_checkpoint(job_id, CP_REPO_CLONED, "done", {"sha": sha})
            log.info("[1/12] Cloned at %s", sha[:8])
        except Exception as exc:
            store.set_checkpoint(job_id, CP_REPO_CLONED, "blocked", {"error": str(exc)})
            log.error("[1/12] Clone failed: %s", exc)
            store.update_job_status(job_id, "blocked")
            return  # Cannot continue without repo
    checkpoints = store.get_checkpoints(job_id)

    # --- Step 2: Detect stack ------------------------------------------------
    stack_profile: dict = {}
    if not _is_done(checkpoints, CP_STACK_DETECTED):
        log.info("[2/12] Detecting technology stack")
        try:
            from backend.agent import detector
            stack_profile = detector.detect(repo_dir)
            store.set_checkpoint(job_id, CP_STACK_DETECTED, "done", stack_profile)
            log.info(
                "[2/12] Stack detected — languages: %s, frameworks: %s",
                stack_profile.get("languages"),
                stack_profile.get("frameworks"),
            )
        except Exception as exc:
            store.set_checkpoint(job_id, CP_STACK_DETECTED, "blocked", {"error": str(exc)})
            log.error("[2/12] Stack detection failed: %s", exc)
    else:
        cp = store.get_checkpoint(job_id, CP_STACK_DETECTED)
        if cp and isinstance(cp.get("evidence"), dict):
            stack_profile = cp["evidence"]
    checkpoints = store.get_checkpoints(job_id)

    # --- Step 3: Install dependencies ----------------------------------------
    if not _is_done(checkpoints, CP_DEPS_INSTALLED):
        log.info("[3/12] Installing dependencies")
        try:
            from backend.agent import builder
            result = builder.install_deps(repo_dir, stack_profile, job_id)
            log.info("[3/12] Dependencies installed (status=%s)", result["status"])
        except Exception as exc:
            store.set_checkpoint(job_id, CP_DEPS_INSTALLED, "blocked", {"error": str(exc)})
            log.error("[3/12] Dependency install failed: %s", exc)
    checkpoints = store.get_checkpoints(job_id)

    # --- Step 4: Build -------------------------------------------------------
    if not _is_done(checkpoints, CP_BUILD_DONE):
        log.info("[4/12] Running build")
        try:
            from backend.agent import builder
            result = builder.build(repo_dir, stack_profile, job_id)
            log.info("[4/12] Build complete (status=%s)", result["status"])
        except Exception as exc:
            store.set_checkpoint(job_id, CP_BUILD_DONE, "blocked", {"error": str(exc)})
            log.error("[4/12] Build failed: %s", exc)
    checkpoints = store.get_checkpoints(job_id)

    # --- Step 5: Run tests ---------------------------------------------------
    if not _is_done(checkpoints, CP_TESTS_RUN):
        log.info("[5/12] Running tests")
        try:
            from backend.agent import builder
            result = builder.run_tests(repo_dir, stack_profile, job_id)
            log.info("[5/12] Tests complete (exit_code=%s)", result["exit_code"])
        except Exception as exc:
            store.set_checkpoint(job_id, CP_TESTS_RUN, "blocked", {"error": str(exc)})
            log.error("[5/12] Tests failed: %s", exc)
    checkpoints = store.get_checkpoints(job_id)

    # --- Step 6: Start application -------------------------------------------
    app_proc = None
    base_url = ""
    if not _is_done(checkpoints, CP_APP_LAUNCHED):
        log.info("[6/12] Starting application")
        try:
            from backend.agent import builder
            app_proc = builder.start_app(repo_dir, stack_profile, job_id)
            cp = store.get_checkpoint(job_id, CP_APP_LAUNCHED)
            if cp and isinstance(cp.get("evidence"), dict):
                port = cp["evidence"].get("port")
                if port:
                    base_url = f"http://localhost:{port}"
            log.info("[6/12] Application launched (base_url=%s)", base_url or "N/A")
        except Exception as exc:
            store.set_checkpoint(job_id, CP_APP_LAUNCHED, "blocked", {"error": str(exc)})
            log.error("[6/12] Application launch failed: %s", exc)
    else:
        cp = store.get_checkpoint(job_id, CP_APP_LAUNCHED)
        if cp and isinstance(cp.get("evidence"), dict):
            port = cp["evidence"].get("port")
            if port:
                base_url = f"http://localhost:{port}"
    checkpoints = store.get_checkpoints(job_id)

    # --- Step 7: GUI inspection ----------------------------------------------
    ui_map: dict = {"routes": [], "base_url": ""}
    if not _is_done(checkpoints, CP_GUI_INSPECTED):
        log.info("[7/12] Inspecting GUI")
        try:
            from backend.agent import inspector
            ui_map_obj = inspector.inspect(repo_dir, stack_profile, job_id, base_url)
            ui_map = dict(ui_map_obj)
            log.info("[7/12] GUI inspection complete (%d routes)", len(ui_map.get("routes", [])))
        except Exception as exc:
            store.set_checkpoint(job_id, CP_GUI_INSPECTED, "blocked", {"error": str(exc)})
            log.error("[7/12] GUI inspection failed: %s", exc)
    else:
        cp = store.get_checkpoint(job_id, CP_GUI_INSPECTED)
        if cp and isinstance(cp.get("evidence"), dict):
            routes = [{"path": r, "title": "", "description": ""} for r in cp["evidence"].get("routes", [])]
            ui_map = {"routes": routes, "base_url": cp["evidence"].get("base_url", "")}
    checkpoints = store.get_checkpoints(job_id)

    # --- Step 8: Generate user guide -----------------------------------------
    if not _is_done(checkpoints, CP_USER_GUIDE):
        log.info("[8/12] Generating End User Guide")
        try:
            from backend.agent import doc_generator
            html = doc_generator.generate_user_guide(
                job_id, stack_profile, ui_map, checkpoints, repo_dir
            )
            out_path = str(output_dir / f"{job_id}_user_guide.pdf")
            _render_document(html, out_path, "End User Guide", job_id, "user_guide")
            store.set_checkpoint(job_id, CP_USER_GUIDE, "done", {"sections": 13})
            log.info("[8/12] End User Guide saved: %s", out_path)
        except Exception as exc:
            store.set_checkpoint(job_id, CP_USER_GUIDE, "blocked", {"error": str(exc)})
            log.error("[8/12] User Guide generation failed: %s\n%s", exc, traceback.format_exc())
    checkpoints = store.get_checkpoints(job_id)

    # --- Step 9: Generate developer guide ------------------------------------
    if not _is_done(checkpoints, CP_DEV_GUIDE):
        log.info("[9/12] Generating Developer Guide")
        try:
            from backend.agent import doc_generator
            html = doc_generator.generate_dev_guide(
                job_id, stack_profile, checkpoints, repo_dir
            )
            out_path = str(output_dir / f"{job_id}_dev_guide.pdf")
            _render_document(html, out_path, "Developer Guide", job_id, "dev_guide")
            store.set_checkpoint(job_id, CP_DEV_GUIDE, "done", {"sections": 14})
            log.info("[9/12] Developer Guide saved: %s", out_path)
        except Exception as exc:
            store.set_checkpoint(job_id, CP_DEV_GUIDE, "blocked", {"error": str(exc)})
            log.error("[9/12] Developer Guide generation failed: %s\n%s", exc, traceback.format_exc())
    checkpoints = store.get_checkpoints(job_id)

    # --- Step 10: Inject chat view -------------------------------------------
    if not _is_done(checkpoints, CP_CHAT_INJECTED):
        log.info("[10/12] Injecting chat widget")
        try:
            from backend.agent import chat_injector
            chat_injector.inject(repo_dir, stack_profile, ui_map, job_id)
            log.info("[10/12] Chat widget injected")
        except Exception as exc:
            store.set_checkpoint(job_id, CP_CHAT_INJECTED, "blocked", {"error": str(exc)})
            log.error("[10/12] Chat injection failed: %s", exc)
    checkpoints = store.get_checkpoints(job_id)

    # --- Step 11: Re-run tests -----------------------------------------------
    if not _is_done(checkpoints, CP_TESTS_RERUN):
        log.info("[11/12] Re-running tests after chat injection")
        try:
            from backend.agent import builder
            result = builder.run_tests(repo_dir, stack_profile, job_id, checkpoint_name=CP_TESTS_RERUN)
            log.info("[11/12] Re-test complete (exit_code=%s)", result["exit_code"])
        except Exception as exc:
            store.set_checkpoint(job_id, CP_TESTS_RERUN, "blocked", {"error": str(exc)})
            log.error("[11/12] Re-test failed: %s", exc)
    checkpoints = store.get_checkpoints(job_id)

    # Stop app if we started it
    if app_proc is not None:
        try:
            from backend.agent.builder import stop_app
            stop_app(app_proc)
            log.debug("Application process stopped")
        except Exception as exc:
            log.warning("Failed to stop application process: %s", exc)

    # --- Step 12: Persist memory + produce output schema --------------------
    if not _is_done(checkpoints, CP_MEMORY_SAVED):
        log.info("[12/12] Persisting memory and writing output schema")
        try:
            _save_output_schema(job_id, repo_url, stack_profile, checkpoints, output_dir)
            store.set_checkpoint(job_id, CP_MEMORY_SAVED, "done", {})
            log.info("[12/12] Output schema written")
        except Exception as exc:
            store.set_checkpoint(job_id, CP_MEMORY_SAVED, "blocked", {"error": str(exc)})
            log.error("[12/12] Output schema write failed: %s", exc)

    store.update_job_status(job_id, "done")
    log.info("Job %d complete", job_id)


def resume_job(job_id: int) -> None:
    """Reload state and resume from last completed checkpoint."""
    log.info("Resuming job %d", job_id)
    run_job(job_id)


def _save_output_schema(
    job_id: int,
    repo_url: str,
    stack_profile: dict,
    checkpoints: list[dict],
    output_dir: Path,
) -> None:
    """Write outputs/{job_id}_summary.json with the full output schema."""
    from backend.agent.acceptance_check import evaluate_acceptance_criteria

    knowledge = store.get_learned_knowledge(job_id)
    interactions = store.get_user_interactions(job_id)

    last_question = knowledge[-1]["question"] if knowledge else None

    acceptance = evaluate_acceptance_criteria(job_id)

    schema = {
        "job_id": job_id,
        "repo_url": repo_url,
        "stack_profile": stack_profile,
        "checkpoints": [
            {"name": c["checkpoint_name"], "status": c["status"]}
            for c in checkpoints
        ],
        "documents": [d for d in store.get_documents(job_id)],
        "memory_learning_log": {
            "learned_knowledge_count": len(knowledge),
            "user_interaction_count": len(interactions),
            "last_learned_question": last_question,
        },
        "acceptance_criteria": acceptance,
    }

    out_path = output_dir / f"{job_id}_summary.json"
    out_path.write_text(json.dumps(schema, indent=2))
