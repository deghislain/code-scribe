"""Repository clone, update, diff, and polling utilities."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import git

from backend.logger import get_logger

log = get_logger(__name__)


def clone_or_update(repo_url: str, dest_dir: str) -> str:
    """
    Clone *repo_url* into *dest_dir* if it doesn't exist, or fetch + pull if it does.
    Returns the current HEAD commit SHA.
    """
    dest = Path(dest_dir)
    if dest.exists() and (dest / ".git").exists():
        log.info("Updating existing repo at %s", dest_dir)
        repo = git.Repo(str(dest))
        origin = repo.remotes.origin
        origin.fetch()
        repo.git.pull("--ff-only")
    else:
        log.info("Cloning %s → %s", repo_url, dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        repo = git.Repo.clone_from(repo_url, str(dest))
    sha = repo.head.commit.hexsha
    log.debug("HEAD at %s", sha[:8])
    return sha


def get_current_sha(repo_dir: str) -> str:
    """Return the current HEAD commit SHA for *repo_dir*."""
    return git.Repo(repo_dir).head.commit.hexsha


def get_changed_files(repo_dir: str, old_sha: str, new_sha: str) -> list[str]:
    """
    Return the list of files changed between *old_sha* and *new_sha*.
    """
    repo = git.Repo(repo_dir)
    old_commit = repo.commit(old_sha)
    new_commit = repo.commit(new_sha)
    diff = old_commit.diff(new_commit)
    changed: list[str] = []
    for item in diff:
        if item.a_path:
            changed.append(item.a_path)
        if item.b_path and item.b_path != item.a_path:
            changed.append(item.b_path)
    return list(set(changed))


def poll_for_changes(
    repo_url: str,
    repo_dir: str,
    last_known_sha: str,
) -> Optional[str]:
    """
    Fetch remote HEAD; return new SHA if it differs from *last_known_sha*, else None.
    Returns None if last_known_sha is empty/None (no baseline to compare against).
    """
    if not last_known_sha:
        return None
    log.debug("Polling for changes in %s", repo_dir)
    repo = git.Repo(repo_dir)
    origin = repo.remotes.origin
    origin.fetch()
    # Use the tracking branch tip rather than the remote HEAD symbolic ref,
    # which may not exist on shallow/partial clones.
    try:
        remote_sha = origin.refs[repo.active_branch.name].commit.hexsha
    except (IndexError, TypeError):
        # Fallback: read the remote HEAD commit via ls-remote
        log.debug("Falling back to ls-remote for HEAD resolution")
        result = repo.git.ls_remote("origin", "HEAD")
        remote_sha = result.split()[0] if result else last_known_sha
    if remote_sha != last_known_sha:
        log.info("New remote SHA: %s (was %s)", remote_sha[:8], last_known_sha[:8])
        return remote_sha
    return None
