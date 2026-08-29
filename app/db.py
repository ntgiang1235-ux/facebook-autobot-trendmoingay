import os

import libsql
from dotenv import load_dotenv

from db_retry import run_with_retry

load_dotenv()

TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL", "").strip()
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "").strip()


def validate_config() -> None:
    missing = []
    if not TURSO_DATABASE_URL:
        missing.append("TURSO_DATABASE_URL")
    if not TURSO_AUTH_TOKEN:
        missing.append("TURSO_AUTH_TOKEN")
    if missing:
        raise RuntimeError("Thiếu cấu hình Turso: " + ", ".join(missing))


def _execute_once(query: str, params: tuple = ()) -> list[tuple]:
    validate_config()
    conn = libsql.connect(database=TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN)
    try:
        cur = conn.execute(query, params)
        conn.commit()
        return cur.fetchall()
    finally:
        conn.close()


def execute(query: str, params: tuple = ()) -> list[tuple]:
    """Execute one Turso statement with retry for transient upstream failures."""
    return run_with_retry(lambda: _execute_once(query, params))


def ensure_schema() -> None:
    execute(
        """
        CREATE TABLE IF NOT EXISTS job_runs (
            run_key TEXT PRIMARY KEY,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            detail TEXT
        )
        """
    )


def record_job(
    run_key: str,
    action: str,
    status: str,
    started_at: str,
    finished_at: str | None = None,
    detail: str = "",
) -> None:
    execute(
        """
        INSERT INTO job_runs (run_key, action, status, started_at, finished_at, detail)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_key) DO UPDATE SET
            action = excluded.action,
            status = excluded.status,
            started_at = excluded.started_at,
            finished_at = excluded.finished_at,
            detail = excluded.detail
        """,
        (run_key, action, status, started_at, finished_at, detail),
    )
