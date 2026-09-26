"""Tests for backend/agent/sync_monitor.py."""
import pytest
from unittest.mock import patch, MagicMock, call
from pathlib import Path

from backend.agent.sync_monitor import _repo_name, _map_to_sections, sync_check
from backend.memory import store


class TestRepoName:
    def test_basic(self):
        assert _repo_name("https://github.com/user/myrepo") == "myrepo"

    def test_strips_git_suffix(self):
        assert _repo_name("https://github.com/user/myrepo.git") == "myrepo"

    def test_trailing_slash(self):
        assert _repo_name("https://github.com/user/myrepo/") == "myrepo"


class TestMapToSections:
    def test_python_source_goes_to_dev_guide(self):
        result = _map_to_sections(["backend/app.py"])
        assert "dev_guide" in result

    def test_js_source_goes_to_dev_guide(self):
        result = _map_to_sections(["src/index.js"])
        assert "dev_guide" in result

    def test_readme_goes_to_both(self):
        result = _map_to_sections(["README.md"])
        assert "user_guide" in result
        assert "dev_guide" in result

    def test_config_goes_to_dev_guide(self):
        result = _map_to_sections(["config/settings.py"])
        assert "dev_guide" in result

    def test_test_files_go_to_dev_guide(self):
        result = _map_to_sections(["tests/test_app.py"])
        assert "dev_guide" in result

    def test_unrelated_file_no_section(self):
        result = _map_to_sections(["CODEOWNERS"])
        assert len(result) == 0

    def test_docs_folder_both_guides(self):
        result = _map_to_sections(["docs/api.md"])
        assert "user_guide" in result
        assert "dev_guide" in result

    def test_ts_file_goes_to_dev_guide(self):
        result = _map_to_sections(["src/app.ts"])
        assert "dev_guide" in result

    def test_env_file_goes_to_dev_guide(self):
        result = _map_to_sections([".env"])
        assert "dev_guide" in result

    def test_src_folder_goes_to_dev_guide(self):
        result = _map_to_sections(["src/main.cpp"])
        assert "dev_guide" in result


class TestSyncCheck:
    def test_skips_when_no_active_job(self, monkeypatch):
        monkeypatch.setattr("backend.agent.sync_monitor.store.get_active_job", lambda: None)
        sync_check()  # Should not raise

    def test_skips_when_job_not_done(self, monkeypatch):
        monkeypatch.setattr(
            "backend.agent.sync_monitor.store.get_active_job",
            lambda: {"id": 1, "repo_url": "https://x.com/r", "status": "running", "commit_sha": "abc"},
        )
        sync_check()  # Should not raise

    def test_skips_when_no_new_commits(self, job_id, monkeypatch, tmp_path):
        store.update_job_status(job_id, "done")
        store.update_job_commit(job_id, "abc123")

        monkeypatch.setattr(
            "backend.agent.sync_monitor.store.get_active_job",
            lambda: {"id": job_id, "repo_url": "https://x.com/r",
                     "status": "done", "commit_sha": "abc123"},
        )
        monkeypatch.setattr("backend.agent.sync_monitor.settings.WORKSPACE_DIR", str(tmp_path))

        mock_repo = MagicMock()
        mock_repo.poll_for_changes.return_value = None

        # sync_check does `from backend.agent import repo as repo_mod` inside the function.
        # After the real `backend.agent.repo` module has been imported (e.g. by test_repo.py),
        # we must patch both sys.modules AND the package attribute to guarantee the mock is used.
        import backend.agent as _agent_pkg
        with patch.dict("sys.modules", {"backend.agent.repo": mock_repo}), \
             patch.object(_agent_pkg, "repo", mock_repo, create=True):
            sync_check()

        # No sync checkpoint should have been written
        cp = store.get_checkpoint(job_id, "sync_completed")
        assert cp is None

    def test_regenerates_on_new_commit(self, job_id, monkeypatch, tmp_path):
        store.update_job_status(job_id, "done")
        store.update_job_commit(job_id, "old_sha")

        monkeypatch.setattr(
            "backend.agent.sync_monitor.store.get_active_job",
            lambda: {"id": job_id, "repo_url": "https://x.com/r",
                     "status": "done", "commit_sha": "old_sha"},
        )
        monkeypatch.setattr("backend.agent.sync_monitor.settings.WORKSPACE_DIR", str(tmp_path))
        monkeypatch.setattr("backend.agent.sync_monitor.settings.OUTPUT_DIR", str(tmp_path))

        mock_repo = MagicMock()
        mock_repo.poll_for_changes.return_value = "new_sha"
        mock_repo.get_changed_files.return_value = ["README.md"]
        mock_repo.clone_or_update.return_value = "new_sha"

        mock_regen = MagicMock()

        import backend.agent.sync_monitor as sm
        import backend.agent as _agent_pkg
        with patch.dict("sys.modules", {"backend.agent.repo": mock_repo}), \
             patch.object(_agent_pkg, "repo", mock_repo, create=True), \
             patch.object(sm, "_regenerate", mock_regen):
            sync_check()

        mock_regen.assert_called_once()
        # Job commit should be updated
        assert store.get_job(job_id)["commit_sha"] == "new_sha"
