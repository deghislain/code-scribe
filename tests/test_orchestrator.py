"""Tests for backend/agent/orchestrator.py — all external dependencies mocked."""
import contextlib
import json
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from backend.agent import orchestrator
from backend.memory import store


@contextlib.contextmanager
def _patch_agent_modules(module_map: dict):
    """
    Patch agent sub-modules robustly: update both sys.modules AND the
    backend.agent package attributes so that lazy `from backend.agent import X`
    statements always see the mock regardless of prior imports in the session.
    """
    import backend.agent as _agent_pkg

    saved_attr = {k: getattr(_agent_pkg, k, None) for k in module_map}
    saved_sys = {f"backend.agent.{k}": sys.modules.get(f"backend.agent.{k}") for k in module_map}

    for k, v in module_map.items():
        setattr(_agent_pkg, k, v)
        sys.modules[f"backend.agent.{k}"] = v
    try:
        yield
    finally:
        for k in module_map:
            if saved_attr[k] is None:
                try:
                    delattr(_agent_pkg, k)
                except AttributeError:
                    pass
            else:
                setattr(_agent_pkg, k, saved_attr[k])
            orig = saved_sys[f"backend.agent.{k}"]
            if orig is None:
                sys.modules.pop(f"backend.agent.{k}", None)
            else:
                sys.modules[f"backend.agent.{k}"] = orig


def _make_mock_repo(sha="abc123"):
    mock = MagicMock()
    mock.clone_or_update.return_value = sha
    mock.get_current_sha.return_value = sha
    return mock


def _make_mock_builder(status="done"):
    mock = MagicMock()
    r = {"status": status, "command": "cmd", "stdout": "", "stderr": "", "exit_code": 0}
    mock.install_deps.return_value = r
    mock.build.return_value = r
    mock.run_tests.return_value = r
    mock.start_app.return_value = None
    return mock


def _make_mock_detector():
    mock = MagicMock()
    mock.detect.return_value = {
        "languages": ["Python"],
        "frameworks": [],
        "build_system": "setuptools/pyproject",
        "test_frameworks": ["pytest"],
        "has_gui": False,
        "gui_type": "none",
        "entry_points": [],
        "package_managers": ["pip"],
    }
    return mock


class TestIsDone:
    def test_true_when_checkpoint_done(self):
        cps = [{"checkpoint_name": "step_a", "status": "done"}]
        assert orchestrator._is_done(cps, "step_a") is True

    def test_false_when_missing(self):
        assert orchestrator._is_done([], "step_a") is False

    def test_false_when_not_done(self):
        cps = [{"checkpoint_name": "step_a", "status": "blocked"}]
        assert orchestrator._is_done(cps, "step_a") is False


class TestRepoName:
    def test_basic(self):
        assert orchestrator._repo_name("https://github.com/user/myrepo") == "myrepo"

    def test_strips_git(self):
        assert orchestrator._repo_name("https://github.com/user/myrepo.git") == "myrepo"


class TestRunJob:
    """Integration-level tests for orchestrator.run_job using mocked modules."""

    def _mock_all(self, monkeypatch, tmp_path):
        """Patch all agent modules and filesystem-related settings."""
        monkeypatch.setattr(orchestrator.settings, "WORKSPACE_DIR", str(tmp_path / "workspaces"))
        monkeypatch.setattr(orchestrator.settings, "OUTPUT_DIR", str(tmp_path / "outputs"))

        mock_repo = _make_mock_repo()
        mock_builder = _make_mock_builder()
        mock_detector = _make_mock_detector()

        mock_inspector = MagicMock()
        mock_inspector.inspect.return_value = {"routes": [], "base_url": ""}

        mock_doc_gen = MagicMock()
        mock_doc_gen.generate_user_guide.return_value = "<html>user</html>"
        mock_doc_gen.generate_dev_guide.return_value = "<html>dev</html>"

        mock_pdf = MagicMock()

        mock_injector = MagicMock()

        mock_accept = MagicMock()
        mock_accept.evaluate_acceptance_criteria.return_value = {}

        return {
            "repo": mock_repo,
            "builder": mock_builder,
            "detector": mock_detector,
            "inspector": mock_inspector,
            "doc_generator": mock_doc_gen,
            "pdf_renderer": mock_pdf,
            "chat_injector": mock_injector,
            "acceptance_check": mock_accept,
        }

    def test_job_not_found_returns_early(self, monkeypatch):
        # Job 99999 does not exist
        orchestrator.run_job(99999)  # should not raise

    def test_full_run_happy_path(self, job_id, monkeypatch, tmp_path):
        mocks = self._mock_all(monkeypatch, tmp_path)

        with _patch_agent_modules({
            "repo": mocks["repo"],
            "builder": mocks["builder"],
            "detector": mocks["detector"],
            "inspector": mocks["inspector"],
            "doc_generator": mocks["doc_generator"],
            "pdf_renderer": mocks["pdf_renderer"],
            "chat_injector": mocks["chat_injector"],
            "acceptance_check": mocks["acceptance_check"],
        }):
            orchestrator.run_job(job_id)

        job = store.get_job(job_id)
        assert job["status"] == "done"

    def test_clone_failure_sets_blocked_and_returns(self, job_id, monkeypatch, tmp_path):
        mocks = self._mock_all(monkeypatch, tmp_path)

        failing_repo = MagicMock()
        failing_repo.clone_or_update.side_effect = Exception("network error")

        with _patch_agent_modules({"repo": failing_repo}):
            orchestrator.run_job(job_id)

        cp = store.get_checkpoint(job_id, "repo_cloned")
        assert cp is not None
        assert cp["status"] == "blocked"
        job = store.get_job(job_id)
        assert job["status"] == "blocked"

    def test_resumes_from_existing_checkpoint(self, job_id, monkeypatch, tmp_path):
        """If repo_cloned is already done, clone_or_update should NOT be called again."""
        store.set_checkpoint(job_id, "repo_cloned", "done", {"sha": "abc123"})
        store.update_job_commit(job_id, "abc123")

        mocks = self._mock_all(monkeypatch, tmp_path)

        with _patch_agent_modules({
            "repo": mocks["repo"],
            "builder": mocks["builder"],
            "detector": mocks["detector"],
            "inspector": mocks["inspector"],
            "doc_generator": mocks["doc_generator"],
            "pdf_renderer": mocks["pdf_renderer"],
            "chat_injector": mocks["chat_injector"],
            "acceptance_check": mocks["acceptance_check"],
        }):
            orchestrator.run_job(job_id)

        # clone_or_update must not have been called
        mocks["repo"].clone_or_update.assert_not_called()

    def test_resume_job_delegates_to_run_job(self, job_id, monkeypatch):
        called = []
        monkeypatch.setattr(orchestrator, "run_job", lambda jid: called.append(jid))
        orchestrator.resume_job(job_id)
        assert called == [job_id]
