"""
Round-trip test for backend/memory/store.py.

Patches config.settings.DB_PATH to a temp file so it never touches the
real database. Cleans up on exit.
"""

import os
import sys
import tempfile

# ---------------------------------------------------------------------------
# Provide required env vars and patch DB_PATH before importing store so
# every connection uses the temp DB and config validation passes.
# ---------------------------------------------------------------------------
os.environ.setdefault("GROQ_API_KEY", "test-key")

import backend.config as _cfg  # noqa: E402

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
_cfg.settings.DB_PATH = _tmp.name

import backend.memory.store as store  # noqa: E402  (import after patch)


def test_all() -> None:
    # ---- init ---------------------------------------------------------------
    store.init_db()

    # ---- upsert_job: insert -------------------------------------------------
    job_id = store.upsert_job("https://github.com/example/repo", branch="main", commit_sha="abc123")
    assert isinstance(job_id, int) and job_id > 0, "upsert_job should return a positive int"

    # ---- get_job ------------------------------------------------------------
    job = store.get_job(job_id)
    assert job is not None, "get_job returned None"
    assert job["repo_url"] == "https://github.com/example/repo"
    assert job["branch"] == "main"
    assert job["commit_sha"] == "abc123"
    assert job["status"] == "pending"

    # ---- get_active_job -----------------------------------------------------
    active = store.get_active_job()
    assert active is not None and active["id"] == job_id

    # ---- upsert_job: update (same repo_url) ---------------------------------
    job_id2 = store.upsert_job("https://github.com/example/repo", commit_sha="def456")
    assert job_id2 == job_id, "upsert should return same id for duplicate repo_url"
    job = store.get_job(job_id)
    assert job["commit_sha"] == "def456", "upsert should update commit_sha"

    # ---- update_job_status --------------------------------------------------
    store.update_job_status(job_id, "running")
    job = store.get_job(job_id)
    assert job["status"] == "running"

    # ---- update_job_commit --------------------------------------------------
    store.update_job_commit(job_id, "ghi789")
    job = store.get_job(job_id)
    assert job["commit_sha"] == "ghi789"

    # ---- set_checkpoint (dict evidence) -------------------------------------
    store.set_checkpoint(job_id, "clone_repo", "passed", evidence={"files": 42})
    store.set_checkpoint(job_id, "lint_check", "failed", evidence="raw string evidence")
    store.set_checkpoint(job_id, "build", "passed")

    # ---- get_checkpoints ----------------------------------------------------
    cps = store.get_checkpoints(job_id)
    assert len(cps) == 3
    clone_cp = next(c for c in cps if c["checkpoint_name"] == "clone_repo")
    assert clone_cp["status"] == "passed"
    assert clone_cp["evidence"] == {"files": 42}, "dict evidence should round-trip"

    lint_cp = next(c for c in cps if c["checkpoint_name"] == "lint_check")
    assert lint_cp["evidence"] == "raw string evidence", "non-JSON string evidence should be preserved"

    # ---- get_checkpoint (single) --------------------------------------------
    cp = store.get_checkpoint(job_id, "build")
    assert cp is not None and cp["status"] == "passed"
    assert store.get_checkpoint(job_id, "nonexistent") is None

    # ---- upsert checkpoint (ON CONFLICT update) -----------------------------
    store.set_checkpoint(job_id, "clone_repo", "updated", evidence={"files": 99})
    cp_updated = store.get_checkpoint(job_id, "clone_repo")
    assert cp_updated["status"] == "updated"
    assert cp_updated["evidence"] == {"files": 99}
    assert len(store.get_checkpoints(job_id)) == 3, "upsert must not add a duplicate row"

    # ---- save_document / get_documents --------------------------------------
    store.save_document(job_id, "readme", "/outputs/readme.md")
    store.save_document(job_id, "api_docs", "/outputs/api.md")
    docs = store.get_documents(job_id)
    assert len(docs) == 2
    assert docs[0]["doc_type"] == "readme"
    assert docs[0]["file_path"] == "/outputs/readme.md"

    # ---- save_learned_knowledge / get_learned_knowledge ---------------------
    store.save_learned_knowledge(job_id, "What does foo() do?", "It foos.", evidence="src/foo.py:10", confidence=0.9)
    knowledge = store.get_learned_knowledge(job_id)
    assert len(knowledge) == 1
    k = knowledge[0]
    assert k["question"] == "What does foo() do?"
    assert k["answer"] == "It foos."
    assert k["source_evidence"] == "src/foo.py:10"
    assert abs(k["confidence"] - 0.9) < 1e-9

    # ---- save_user_interaction / get_user_interactions ----------------------
    store.save_user_interaction(job_id, "Hello?", "Hi there!", validated=True)
    store.save_user_interaction(job_id, "What now?", "Keep going.")
    interactions = store.get_user_interactions(job_id)
    assert len(interactions) == 2
    assert interactions[0]["user_message"] == "Hello?"
    assert interactions[0]["validated"] == 1  # stored as int
    assert interactions[1]["validated"] == 0

    print("ALL TESTS PASSED")


if __name__ == "__main__":
    try:
        test_all()
    finally:
        os.unlink(_tmp.name)
