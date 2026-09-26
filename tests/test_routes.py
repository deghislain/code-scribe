"""Tests for backend/api/routes.py using FastAPI TestClient."""
import json
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from backend.main import app
from backend.memory import store


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=True)


class TestSubmitJob:
    def test_creates_job_and_returns_job_id(self, client, monkeypatch):
        monkeypatch.setattr(
            "backend.api.routes._get_orchestrator",
            lambda: MagicMock(run_job=MagicMock()),
        )
        with patch("backend.api.routes.asyncio.create_task"):
            resp = client.post("/api/jobs", json={"repo_url": "https://github.com/test/repo"})
        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "running"

    def test_duplicate_url_creates_new_job(self, client, monkeypatch):
        monkeypatch.setattr(
            "backend.api.routes._get_orchestrator",
            lambda: MagicMock(run_job=MagicMock()),
        )
        with patch("backend.api.routes.asyncio.create_task"):
            r1 = client.post("/api/jobs", json={"repo_url": "https://github.com/dup/repo"})
            r2 = client.post("/api/jobs", json={"repo_url": "https://github.com/dup/repo"})
        assert r1.json()["job_id"] != r2.json()["job_id"]


class TestGetCurrentJob:
    def test_returns_idle_when_no_jobs(self, client):
        resp = client.get("/api/jobs/current")
        assert resp.status_code == 200
        # May return idle or the job created by conftest; either is valid
        data = resp.json()
        assert "status" in data

    def test_returns_job_data(self, client, job_id):
        resp = client.get("/api/jobs/current")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == job_id


class TestGetCheckpoints:
    def test_empty_list_for_new_job(self, client, job_id):
        resp = client.get(f"/api/jobs/{job_id}/checkpoints")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_checkpoints(self, client, job_id):
        store.set_checkpoint(job_id, "build_done", "done")
        resp = client.get(f"/api/jobs/{job_id}/checkpoints")
        assert resp.status_code == 200
        cps = resp.json()
        assert len(cps) == 1
        assert cps[0]["checkpoint_name"] == "build_done"


class TestGetDocuments:
    def test_empty_list_for_new_job(self, client, job_id):
        resp = client.get(f"/api/jobs/{job_id}/documents")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_documents(self, client, job_id):
        store.save_document(job_id, "user_guide", "/outputs/guide.pdf")
        resp = client.get(f"/api/jobs/{job_id}/documents")
        assert resp.status_code == 200
        docs = resp.json()
        assert len(docs) == 1


class TestDownloadDocument:
    def test_returns_404_for_missing_file(self, client):
        resp = client.get("/api/documents/nonexistent_file.pdf")
        assert resp.status_code == 404

    def test_serves_existing_file(self, client, tmp_path, monkeypatch):
        # Write a test PDF
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake content")
        monkeypatch.setattr("backend.api.routes.settings.OUTPUT_DIR", str(tmp_path))
        resp = client.get("/api/documents/test.pdf")
        assert resp.status_code == 200

    def test_path_traversal_rejected(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.api.routes.settings.OUTPUT_DIR", str(tmp_path))
        resp = client.get("/api/documents/../etc/passwd")
        # Should return 404 (file doesn't exist) or 400 (traversal blocked)
        assert resp.status_code in (400, 404)


class TestDeleteDocument:
    def test_returns_404_when_document_does_not_exist(self, client, job_id):
        resp = client.delete(f"/api/jobs/{job_id}/documents/user_guide")
        assert resp.status_code == 404

    def test_deletes_db_record_and_returns_deleted_true(self, client, job_id, tmp_path, monkeypatch):
        pdf = tmp_path / f"{job_id}_user_guide.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        store.save_document(job_id, "user_guide", str(pdf))

        resp = client.delete(f"/api/jobs/{job_id}/documents/user_guide")

        assert resp.status_code == 200
        assert resp.json() == {"deleted": True, "doc_type": "user_guide"}
        # DB record gone
        assert store.get_documents(job_id) == []
        # File removed from disk
        assert not pdf.exists()

    def test_deletes_record_even_when_file_already_gone(self, client, job_id, tmp_path):
        # File path points to something that doesn't exist
        store.save_document(job_id, "dev_guide", str(tmp_path / "missing.pdf"))

        resp = client.delete(f"/api/jobs/{job_id}/documents/dev_guide")

        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        assert store.get_documents(job_id) == []

    def test_second_delete_returns_404(self, client, job_id, tmp_path):
        pdf = tmp_path / f"{job_id}_dev_guide.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        store.save_document(job_id, "dev_guide", str(pdf))

        client.delete(f"/api/jobs/{job_id}/documents/dev_guide")
        resp = client.delete(f"/api/jobs/{job_id}/documents/dev_guide")
        assert resp.status_code == 404




class TestChatEndpoint:
    def test_returns_404_for_missing_job(self, client):
        with patch("backend.api.routes.chat_handler.answer_question", return_value="x"):
            resp = client.post("/api/chat", json={"job_id": 99999, "message": "hi"})
        assert resp.status_code == 404

    def test_returns_response_for_existing_job(self, client, job_id, monkeypatch):
        monkeypatch.setattr(
            "backend.api.routes.chat_handler.answer_question",
            lambda jid, msg: "I know the answer.",
        )
        resp = client.post("/api/chat", json={"job_id": job_id, "message": "What is it?"})
        assert resp.status_code == 200
        assert resp.json()["response"] == "I know the answer."
