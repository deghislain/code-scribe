import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from backend.config import settings

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_db_connection() -> sqlite3.Connection:
    """Return a connection with row_factory = sqlite3.Row."""
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """
    Create all tables from schema.sql if they don't exist, and run any
    migration statements.  Called once at app startup.

    The schema file may include ALTER TABLE statements for additive migrations.
    SQLite raises OperationalError if the column already exists; those are
    swallowed so the same schema file is safe to run against both fresh and
    existing databases.
    """
    schema = _SCHEMA_PATH.read_text()
    with get_db_connection() as conn:
        for statement in schema.split(";"):
            stmt = statement.strip()
            if not stmt:
                continue
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as exc:
                # Ignore "duplicate column" errors from idempotent ALTER TABLE
                # migrations; re-raise anything else.
                if "duplicate column" not in str(exc).lower():
                    raise


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def create_job(repo_url: str, branch: str = "main", commit_sha: str | None = None) -> int:
    """
    Always insert a new job row and return the new id.
    Use this when the user explicitly triggers a fresh analysis run.
    """
    with get_db_connection() as conn:
        cur = conn.execute(
            "INSERT INTO jobs (repo_url, branch, commit_sha) VALUES (?, ?, ?)",
            (repo_url, branch, commit_sha),
        )
        return cur.lastrowid


def upsert_job(repo_url: str, branch: str = "main", commit_sha: str | None = None) -> int:
    """
    If a job with this repo_url already exists, update commit_sha and updated_at,
    return its id.  If not, insert a new job and return the new id.
    """
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT id FROM jobs WHERE repo_url = ? ORDER BY id DESC LIMIT 1",
            (repo_url,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE jobs SET commit_sha = ?, updated_at = datetime('now') WHERE id = ?",
                (commit_sha, row["id"]),
            )
            return row["id"]
        cur = conn.execute(
            "INSERT INTO jobs (repo_url, branch, commit_sha) VALUES (?, ?, ?)",
            (repo_url, branch, commit_sha),
        )
        return cur.lastrowid


def get_job(job_id: int) -> dict | None:
    """Return job as dict or None."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def get_active_job() -> dict | None:
    """Return the most recent job (any status), or None if no jobs exist."""
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def update_job_status(job_id: int, status: str) -> None:
    """Update status and updated_at for a job."""
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE jobs SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, job_id),
        )


def update_job_commit(job_id: int, commit_sha: str) -> None:
    """Update commit_sha and updated_at for a job."""
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE jobs SET commit_sha = ?, updated_at = datetime('now') WHERE id = ?",
            (commit_sha, job_id),
        )


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

def set_checkpoint(
    job_id: int,
    name: str,
    status: str,
    evidence: dict | str | None = None,
) -> None:
    """
    Insert or replace a checkpoint (UNIQUE on job_id + checkpoint_name).
    evidence is serialized to JSON if it's a dict.
    """
    if isinstance(evidence, dict):
        evidence = json.dumps(evidence)
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO checkpoints (job_id, checkpoint_name, status, evidence)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(job_id, checkpoint_name)
            DO UPDATE SET status = excluded.status,
                          evidence = excluded.evidence,
                          recorded_at = datetime('now')
            """,
            (job_id, name, status, evidence),
        )


def get_checkpoints(job_id: int) -> list[dict]:
    """Return all checkpoints for a job as list of dicts."""
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM checkpoints WHERE job_id = ? ORDER BY id",
            (job_id,),
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        if d.get("evidence"):
            try:
                d["evidence"] = json.loads(d["evidence"])
            except (json.JSONDecodeError, TypeError):
                pass
        result.append(d)
    return result


def get_checkpoint(job_id: int, name: str) -> dict | None:
    """Return a specific checkpoint or None."""
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM checkpoints WHERE job_id = ? AND checkpoint_name = ?",
            (job_id, name),
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    if d.get("evidence"):
        try:
            d["evidence"] = json.loads(d["evidence"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def fmt_generated_at(raw: str | None) -> str:
    """
    Convert the raw UTC timestamp stored by SQLite (``datetime('now')``) into a
    human-readable local date string suitable for the PDF cover page.

    SQLite stores timestamps as ``"YYYY-MM-DD HH:MM:SS"`` in UTC.  This helper
    parses that string, converts it to local time, and returns a string like
    ``"03 June 2025"``.  Falls back to today's local date if *raw* is empty or
    unparseable.
    """
    try:
        if raw:
            utc_dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            local_dt = utc_dt.astimezone()
            return local_dt.strftime("%-d %B %Y")
    except (ValueError, OSError):
        pass
    return datetime.now().strftime("%-d %B %Y")


def save_document(job_id: int, doc_type: str, file_path: str) -> int:
    """
    Upsert a document record (one row per job_id + doc_type).

    On each upsert the version counter increments by one so callers can
    distinguish successive re-generations of the same document within a job.
    Returns the current version number (1-based).
    """
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO documents (job_id, doc_type, file_path, version)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(job_id, doc_type)
            DO UPDATE SET file_path    = excluded.file_path,
                          version      = version + 1,
                          generated_at = datetime('now')
            """,
            (job_id, doc_type, file_path),
        )
        row = conn.execute(
            "SELECT version FROM documents WHERE job_id = ? AND doc_type = ?",
            (job_id, doc_type),
        ).fetchone()
        return row["version"] if row else 1


def delete_document(job_id: int, doc_type: str) -> bool:
    """
    Remove the document record from the DB.
    Returns True if a row was deleted, False if it didn't exist.
    Does NOT delete the file on disk — the caller is responsible for that.
    """
    with get_db_connection() as conn:
        cur = conn.execute(
            "DELETE FROM documents WHERE job_id = ? AND doc_type = ?",
            (job_id, doc_type),
        )
        return cur.rowcount > 0


def get_documents(job_id: int) -> list[dict]:
    """Return one document per doc_type for a job (latest only)."""
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM documents
            WHERE job_id = ?
            GROUP BY doc_type
            ORDER BY doc_type
            """,
            (job_id,),
        ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Learned knowledge
# ---------------------------------------------------------------------------

def save_learned_knowledge(
    job_id: int,
    question: str,
    answer: str,
    evidence: str = "",
    confidence: float = 1.0,
) -> None:
    """Insert a learned knowledge record."""
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO learned_knowledge (job_id, question, answer, source_evidence, confidence)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job_id, question, answer, evidence, confidence),
        )


def get_learned_knowledge(job_id: int) -> list[dict]:
    """Return all learned knowledge for a job."""
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM learned_knowledge WHERE job_id = ? ORDER BY id",
            (job_id,),
        ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# User interactions
# ---------------------------------------------------------------------------

def save_user_interaction(
    job_id: int,
    user_message: str,
    agent_response: str,
    validated: bool = False,
) -> None:
    """Insert a user interaction record."""
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO user_interactions (job_id, user_message, agent_response, validated)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, user_message, agent_response, int(validated)),
        )


def get_user_interactions(job_id: int) -> list[dict]:
    """Return all user interactions for a job."""
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM user_interactions WHERE job_id = ? ORDER BY id",
            (job_id,),
        ).fetchall()
    return [dict(row) for row in rows]
