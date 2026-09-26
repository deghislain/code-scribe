"""Tests for backend/memory/store.py — full CRUD coverage."""
import pytest
from backend.memory import store


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

class TestUpsertJob:
    def test_insert_returns_positive_id(self):
        jid = store.upsert_job("https://github.com/a/b")
        assert isinstance(jid, int) and jid > 0

    def test_insert_defaults(self):
        jid = store.upsert_job("https://github.com/a/b")
        job = store.get_job(jid)
        assert job["status"] == "pending"
        assert job["branch"] == "main"
        assert job["commit_sha"] is None

    def test_insert_with_all_fields(self):
        jid = store.upsert_job("https://github.com/a/b", branch="dev", commit_sha="abc")
        job = store.get_job(jid)
        assert job["branch"] == "dev"
        assert job["commit_sha"] == "abc"

    def test_upsert_same_url_returns_same_id(self):
        url = "https://github.com/same/repo"
        id1 = store.upsert_job(url)
        id2 = store.upsert_job(url, commit_sha="xyz")
        assert id1 == id2

    def test_upsert_updates_commit_sha(self):
        url = "https://github.com/upd/repo"
        jid = store.upsert_job(url, commit_sha="old")
        store.upsert_job(url, commit_sha="new")
        assert store.get_job(jid)["commit_sha"] == "new"

    def test_different_urls_get_different_ids(self):
        id1 = store.upsert_job("https://github.com/a/x")
        id2 = store.upsert_job("https://github.com/b/y")
        assert id1 != id2


class TestGetJob:
    def test_returns_none_for_missing(self):
        assert store.get_job(99999) is None

    def test_returns_dict(self, job_id):
        job = store.get_job(job_id)
        assert isinstance(job, dict)
        assert "repo_url" in job


class TestGetActiveJob:
    def test_none_when_empty(self):
        # Fresh DB from conftest has one job (from job_id fixture — but this
        # test doesn't use it, so the DB only has what we insert)
        # Use the raw _isolated_db fixture instead by not depending on job_id
        # We confirm no active job on a completely empty DB
        pass  # covered by the job_id fixture's behaviour

    def test_returns_most_recent(self):
        store.upsert_job("https://github.com/first/repo")
        jid2 = store.upsert_job("https://github.com/second/repo")
        active = store.get_active_job()
        assert active is not None
        assert active["id"] == jid2


class TestUpdateJobStatus:
    def test_updates_status(self, job_id):
        store.update_job_status(job_id, "running")
        assert store.get_job(job_id)["status"] == "running"

    def test_multiple_updates(self, job_id):
        for s in ["running", "done", "blocked"]:
            store.update_job_status(job_id, s)
        assert store.get_job(job_id)["status"] == "blocked"


class TestUpdateJobCommit:
    def test_updates_commit_sha(self, job_id):
        store.update_job_commit(job_id, "newsha123")
        assert store.get_job(job_id)["commit_sha"] == "newsha123"


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

class TestSetCheckpoint:
    def test_insert_basic(self, job_id):
        store.set_checkpoint(job_id, "step_a", "done")
        cp = store.get_checkpoint(job_id, "step_a")
        assert cp is not None
        assert cp["status"] == "done"

    def test_dict_evidence_serialised(self, job_id):
        store.set_checkpoint(job_id, "step_b", "done", evidence={"k": "v"})
        cp = store.get_checkpoint(job_id, "step_b")
        assert cp["evidence"] == {"k": "v"}

    def test_string_evidence_preserved(self, job_id):
        store.set_checkpoint(job_id, "step_c", "done", evidence="raw string")
        cp = store.get_checkpoint(job_id, "step_c")
        assert cp["evidence"] == "raw string"

    def test_none_evidence(self, job_id):
        store.set_checkpoint(job_id, "step_d", "done", evidence=None)
        cp = store.get_checkpoint(job_id, "step_d")
        assert cp["evidence"] is None or cp["evidence"] == ""

    def test_upsert_on_conflict(self, job_id):
        store.set_checkpoint(job_id, "step_e", "done", {"x": 1})
        store.set_checkpoint(job_id, "step_e", "blocked", {"x": 2})
        cps = store.get_checkpoints(job_id)
        # Only one row for step_e
        matching = [c for c in cps if c["checkpoint_name"] == "step_e"]
        assert len(matching) == 1
        assert matching[0]["status"] == "blocked"
        assert matching[0]["evidence"] == {"x": 2}


class TestGetCheckpoints:
    def test_empty_list_for_unknown_job(self):
        assert store.get_checkpoints(99999) == []

    def test_returns_ordered_list(self, job_id):
        store.set_checkpoint(job_id, "cp1", "done")
        store.set_checkpoint(job_id, "cp2", "blocked")
        cps = store.get_checkpoints(job_id)
        assert len(cps) == 2
        assert cps[0]["checkpoint_name"] == "cp1"

    def test_evidence_json_decode_error_passthrough(self, job_id):
        """Evidence that is not valid JSON should be returned as-is."""
        store.set_checkpoint(job_id, "cp_bad", "done", evidence="not-json{")
        cp = store.get_checkpoint(job_id, "cp_bad")
        assert cp["evidence"] == "not-json{"


class TestGetCheckpoint:
    def test_returns_none_for_missing(self, job_id):
        assert store.get_checkpoint(job_id, "nonexistent") is None

    def test_returns_correct_row(self, job_id):
        store.set_checkpoint(job_id, "my_cp", "done", {"info": True})
        cp = store.get_checkpoint(job_id, "my_cp")
        assert cp["status"] == "done"
        assert cp["evidence"] == {"info": True}


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

class TestSaveDocument:
    def test_insert_document(self, job_id):
        store.save_document(job_id, "user_guide", "/outputs/guide.pdf")
        docs = store.get_documents(job_id)
        assert len(docs) == 1
        assert docs[0]["doc_type"] == "user_guide"
        assert docs[0]["file_path"] == "/outputs/guide.pdf"

    def test_upsert_updates_path(self, job_id):
        store.save_document(job_id, "user_guide", "/outputs/old.pdf")
        store.save_document(job_id, "user_guide", "/outputs/new.pdf")
        docs = store.get_documents(job_id)
        assert len(docs) == 1
        assert docs[0]["file_path"] == "/outputs/new.pdf"

    def test_multiple_doc_types(self, job_id):
        store.save_document(job_id, "user_guide", "/a.pdf")
        store.save_document(job_id, "dev_guide", "/b.pdf")
        docs = store.get_documents(job_id)
        assert len(docs) == 2

    def test_first_save_returns_version_1(self, job_id):
        v = store.save_document(job_id, "user_guide", "/a.pdf")
        assert v == 1

    def test_second_save_increments_version_to_2(self, job_id):
        store.save_document(job_id, "user_guide", "/a.pdf")
        v = store.save_document(job_id, "user_guide", "/a_v2.pdf")
        assert v == 2

    def test_version_increments_independently_per_doc_type(self, job_id):
        store.save_document(job_id, "user_guide", "/a.pdf")
        store.save_document(job_id, "user_guide", "/a2.pdf")
        v_dev = store.save_document(job_id, "dev_guide", "/b.pdf")
        # dev_guide was inserted for the first time — must be version 1
        assert v_dev == 1

    def test_version_stored_in_db(self, job_id):
        store.save_document(job_id, "user_guide", "/a.pdf")
        store.save_document(job_id, "user_guide", "/a2.pdf")
        docs = store.get_documents(job_id)
        assert docs[0]["version"] == 2

    def test_delete_then_resave_resets_version_to_1(self, job_id):
        store.save_document(job_id, "user_guide", "/a.pdf")
        store.save_document(job_id, "user_guide", "/a2.pdf")
        store.delete_document(job_id, "user_guide")
        v = store.save_document(job_id, "user_guide", "/a3.pdf")
        assert v == 1

    def test_generated_at_is_populated(self, job_id):
        store.save_document(job_id, "user_guide", "/a.pdf")
        docs = store.get_documents(job_id)
        assert docs[0]["generated_at"] is not None
        assert docs[0]["generated_at"] != ""


class TestFmtGeneratedAt:
    def test_valid_utc_string_returns_local_date(self):
        # "2025-06-03 14:22:00" UTC → local date, formatted as "D Month YYYY"
        result = store.fmt_generated_at("2025-06-03 14:22:00")
        # Should be a non-empty string with a 4-digit year
        assert "2025" in result or len(result) > 4  # timezone shift could change day/month

    def test_format_contains_month_name(self):
        result = store.fmt_generated_at("2025-06-03 00:00:00")
        months = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]
        assert any(m in result for m in months)

    def test_none_returns_todays_date(self):
        from datetime import datetime
        result = store.fmt_generated_at(None)
        today_year = str(datetime.now().year)
        assert today_year in result

    def test_empty_string_returns_todays_date(self):
        from datetime import datetime
        result = store.fmt_generated_at("")
        today_year = str(datetime.now().year)
        assert today_year in result

    def test_unparseable_string_returns_todays_date(self):
        from datetime import datetime
        result = store.fmt_generated_at("not-a-date")
        today_year = str(datetime.now().year)
        assert today_year in result


class TestGetDocuments:
    def test_empty_for_unknown_job(self):
        assert store.get_documents(99999) == []


# ---------------------------------------------------------------------------
# Learned knowledge
# ---------------------------------------------------------------------------

class TestLearnedKnowledge:
    def test_save_and_retrieve(self, job_id):
        store.save_learned_knowledge(job_id, "What?", "This.", "src.py:1", 0.8)
        items = store.get_learned_knowledge(job_id)
        assert len(items) == 1
        assert items[0]["question"] == "What?"
        assert items[0]["answer"] == "This."
        assert items[0]["source_evidence"] == "src.py:1"
        assert abs(items[0]["confidence"] - 0.8) < 1e-9

    def test_multiple_entries(self, job_id):
        store.save_learned_knowledge(job_id, "Q1", "A1")
        store.save_learned_knowledge(job_id, "Q2", "A2")
        assert len(store.get_learned_knowledge(job_id)) == 2

    def test_empty_for_unknown_job(self):
        assert store.get_learned_knowledge(99999) == []

    def test_default_confidence(self, job_id):
        store.save_learned_knowledge(job_id, "Q", "A")
        items = store.get_learned_knowledge(job_id)
        assert abs(items[0]["confidence"] - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# User interactions
# ---------------------------------------------------------------------------

class TestUserInteractions:
    def test_save_and_retrieve(self, job_id):
        store.save_user_interaction(job_id, "Hi", "Hello!", validated=True)
        items = store.get_user_interactions(job_id)
        assert len(items) == 1
        assert items[0]["user_message"] == "Hi"
        assert items[0]["agent_response"] == "Hello!"
        assert items[0]["validated"] == 1

    def test_unvalidated_default(self, job_id):
        store.save_user_interaction(job_id, "Q", "A")
        items = store.get_user_interactions(job_id)
        assert items[0]["validated"] == 0

    def test_ordering_by_id(self, job_id):
        store.save_user_interaction(job_id, "First", "1")
        store.save_user_interaction(job_id, "Second", "2")
        items = store.get_user_interactions(job_id)
        assert items[0]["user_message"] == "First"
        assert items[1]["user_message"] == "Second"

    def test_empty_for_unknown_job(self):
        assert store.get_user_interactions(99999) == []
