"""Tests for backend/agent/repo.py — all git operations mocked."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, call
from pathlib import Path

from backend.agent import repo


# ---------------------------------------------------------------------------
# clone_or_update
# ---------------------------------------------------------------------------

class TestCloneOrUpdate:
    def test_clones_when_directory_does_not_exist(self, tmp_path):
        dest = tmp_path / "new_repo"
        mock_git_repo = MagicMock()
        mock_git_repo.head.commit.hexsha = "abc123"

        with patch("backend.agent.repo.git.Repo.clone_from", return_value=mock_git_repo) as mock_clone:
            sha = repo.clone_or_update("https://github.com/user/new_repo", str(dest))

        mock_clone.assert_called_once_with("https://github.com/user/new_repo", str(dest))
        assert sha == "abc123"

    def test_clones_when_no_git_dir(self, tmp_path):
        dest = tmp_path / "existing_no_git"
        dest.mkdir()
        # Directory exists but has no .git subdirectory

        mock_git_repo = MagicMock()
        mock_git_repo.head.commit.hexsha = "def456"

        with patch("backend.agent.repo.git.Repo.clone_from", return_value=mock_git_repo):
            sha = repo.clone_or_update("https://github.com/user/repo", str(dest))

        assert sha == "def456"

    def test_fetches_and_pulls_when_git_exists(self, tmp_path):
        dest = tmp_path / "existing_repo"
        dest.mkdir()
        (dest / ".git").mkdir()

        mock_git_repo = MagicMock()
        mock_git_repo.head.commit.hexsha = "newsha789"

        with patch("backend.agent.repo.git.Repo", return_value=mock_git_repo) as mock_repo_cls:
            sha = repo.clone_or_update("https://github.com/user/repo", str(dest))

        mock_git_repo.remotes.origin.fetch.assert_called_once()
        mock_git_repo.git.pull.assert_called_once_with("--ff-only")
        assert sha == "newsha789"


# ---------------------------------------------------------------------------
# get_current_sha
# ---------------------------------------------------------------------------

class TestGetCurrentSha:
    def test_returns_head_sha(self, tmp_path):
        mock_git_repo = MagicMock()
        mock_git_repo.head.commit.hexsha = "headsha"

        with patch("backend.agent.repo.git.Repo", return_value=mock_git_repo):
            sha = repo.get_current_sha(str(tmp_path))

        assert sha == "headsha"


# ---------------------------------------------------------------------------
# get_changed_files
# ---------------------------------------------------------------------------

class TestGetChangedFiles:
    def test_returns_list_of_changed_files(self, tmp_path):
        mock_diff_item = MagicMock()
        mock_diff_item.a_path = "src/main.py"
        mock_diff_item.b_path = "src/main.py"

        mock_diff_item2 = MagicMock()
        mock_diff_item2.a_path = "tests/test_main.py"
        mock_diff_item2.b_path = "tests/test_main.py"

        old_commit = MagicMock()
        old_commit.diff.return_value = [mock_diff_item, mock_diff_item2]

        mock_git_repo = MagicMock()
        mock_git_repo.commit.side_effect = lambda sha: old_commit if sha == "old" else MagicMock()

        with patch("backend.agent.repo.git.Repo", return_value=mock_git_repo):
            changed = repo.get_changed_files(str(tmp_path), "old", "new")

        assert "src/main.py" in changed
        assert "tests/test_main.py" in changed

    def test_includes_renamed_target_path(self, tmp_path):
        mock_diff_item = MagicMock()
        mock_diff_item.a_path = "old_name.py"
        mock_diff_item.b_path = "new_name.py"  # renamed

        old_commit = MagicMock()
        old_commit.diff.return_value = [mock_diff_item]

        mock_git_repo = MagicMock()
        mock_git_repo.commit.return_value = old_commit

        with patch("backend.agent.repo.git.Repo", return_value=mock_git_repo):
            changed = repo.get_changed_files(str(tmp_path), "old", "new")

        assert "old_name.py" in changed
        assert "new_name.py" in changed

    def test_deduplicates_paths(self, tmp_path):
        mock_diff_item = MagicMock()
        mock_diff_item.a_path = "same.py"
        mock_diff_item.b_path = "same.py"

        old_commit = MagicMock()
        old_commit.diff.return_value = [mock_diff_item]

        mock_git_repo = MagicMock()
        mock_git_repo.commit.return_value = old_commit

        with patch("backend.agent.repo.git.Repo", return_value=mock_git_repo):
            changed = repo.get_changed_files(str(tmp_path), "old", "new")

        assert changed.count("same.py") == 1


# ---------------------------------------------------------------------------
# poll_for_changes
# ---------------------------------------------------------------------------

class TestPollForChanges:
    def test_returns_none_when_no_baseline_sha(self, tmp_path):
        result = repo.poll_for_changes("https://x.com/r", str(tmp_path), "")
        assert result is None

    def test_returns_none_when_sha_unchanged(self, tmp_path):
        mock_ref = MagicMock()
        mock_ref.commit.hexsha = "same_sha"

        mock_origin = MagicMock()
        mock_origin.refs = {"main": mock_ref}

        mock_git_repo = MagicMock()
        mock_git_repo.remotes.origin = mock_origin
        mock_git_repo.active_branch.name = "main"

        with patch("backend.agent.repo.git.Repo", return_value=mock_git_repo):
            result = repo.poll_for_changes("https://x.com/r", str(tmp_path), "same_sha")

        assert result is None

    def test_returns_new_sha_when_changed(self, tmp_path):
        mock_ref = MagicMock()
        mock_ref.commit.hexsha = "new_sha"

        mock_origin = MagicMock()
        mock_origin.refs = {"main": mock_ref}

        mock_git_repo = MagicMock()
        mock_git_repo.remotes.origin = mock_origin
        mock_git_repo.active_branch.name = "main"

        with patch("backend.agent.repo.git.Repo", return_value=mock_git_repo):
            result = repo.poll_for_changes("https://x.com/r", str(tmp_path), "old_sha")

        assert result == "new_sha"

    def test_falls_back_to_ls_remote_on_index_error(self, tmp_path):
        """When accessing origin.refs raises IndexError, ls-remote fallback is used."""
        mock_refs = MagicMock()
        mock_refs.__getitem__ = MagicMock(side_effect=IndexError("no ref"))

        mock_origin = MagicMock()
        mock_origin.refs = mock_refs

        mock_git_repo = MagicMock()
        mock_git_repo.remotes.origin = mock_origin
        mock_git_repo.active_branch.name = "main"
        mock_git_repo.git.ls_remote.return_value = "fallback_sha\tHEAD"

        with patch("backend.agent.repo.git.Repo", return_value=mock_git_repo):
            result = repo.poll_for_changes("https://x.com/r", str(tmp_path), "old_sha")

        assert result == "fallback_sha"

    def test_falls_back_gracefully_when_ls_remote_empty(self, tmp_path):
        """When ls-remote returns empty string, last_known_sha is used so None is returned."""
        mock_refs = MagicMock()
        mock_refs.__getitem__ = MagicMock(side_effect=IndexError("no ref"))

        mock_origin = MagicMock()
        mock_origin.refs = mock_refs

        mock_git_repo = MagicMock()
        mock_git_repo.remotes.origin = mock_origin
        mock_git_repo.active_branch.name = "main"
        mock_git_repo.git.ls_remote.return_value = ""  # empty result

        with patch("backend.agent.repo.git.Repo", return_value=mock_git_repo):
            result = repo.poll_for_changes("https://x.com/r", str(tmp_path), "old_sha")

        # Falls back to last_known_sha → remote_sha == last_known_sha → returns None
        assert result is None
