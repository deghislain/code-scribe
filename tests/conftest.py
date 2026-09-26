"""
Shared pytest fixtures for the code-scribe test suite.

Sets GROQ_API_KEY before any import of backend modules so pydantic-settings
validation passes, then patches DB_PATH to a per-test temp file so tests
never touch the real database.
"""
import os
import tempfile
import pytest

# Must be set before the first import of backend.config
os.environ.setdefault("GROQ_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """
    Redirect every test to its own fresh SQLite database.
    Works by patching both settings.DB_PATH and the module-level
    get_db_connection so every call in the test uses the tmp DB.
    """
    import backend.config as cfg
    import backend.memory.store as store

    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(cfg.settings, "DB_PATH", db_file)
    store.init_db()
    yield


@pytest.fixture()
def job_id(_isolated_db):
    """Return a freshly-inserted job id, ready to use in tests."""
    from backend.memory import store
    return store.upsert_job("https://github.com/test/repo", branch="main", commit_sha="aaa111")


@pytest.fixture()
def tmp_repo(tmp_path):
    """Return a Path pointing to a bare temporary directory (acts as repo root)."""
    return tmp_path
