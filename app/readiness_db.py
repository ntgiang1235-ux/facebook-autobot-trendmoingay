import os
import re

import libsql
from dotenv import load_dotenv

from db_retry import run_with_retry

load_dotenv()

TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL", "").strip()
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "").strip()
_COMMENT_PREFIX = re.compile(r"^(?:\s|--[^\n]*(?:\n|$)|/\*.*?\*/)*", re.S)
_TABLE_INFO = re.compile(
    r"(?is)^PRAGMA\s+table_info\s*\(\s*[A-Za-z_][A-Za-z0-9_]*\s*\)\s*$"
)


def validate_config() -> None:
    missing = []
    if not TURSO_DATABASE_URL:
        missing.append("TURSO_DATABASE_URL")
    if not TURSO_AUTH_TOKEN:
        missing.append("TURSO_AUTH_TOKEN")
    if missing:
        raise RuntimeError("Thiếu cấu hình Turso: " + ", ".join(missing))


def _single_statement(query: str) -> str:
    normalized = _COMMENT_PREFIX.sub("", query).strip()
    if normalized.endswith(";"):
        normalized = normalized[:-1].rstrip()
    if ";" in normalized:
        raise ValueError("readiness database rejected stacked statements")
    return normalized


def validate_read_query(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("readiness query must be a non-empty string")

    normalized = _single_statement(query)
    keyword = normalized.split(None, 1)[0].upper() if normalized else ""
    if keyword == "SELECT":
        return keyword
    if keyword == "PRAGMA" and _TABLE_INFO.fullmatch(normalized):
        return keyword
    raise ValueError(
        f"readiness database rejected non-read query: {keyword or 'UNKNOWN'}"
    )


def _execute_read_once(query: str, params: tuple = ()) -> list[tuple]:
    validate_read_query(query)
    validate_config()
    conn = libsql.connect(
        database=TURSO_DATABASE_URL,
        auth_token=TURSO_AUTH_TOKEN,
    )
    try:
        cur = conn.execute(query, params)
        return cur.fetchall()
    finally:
        conn.close()


def execute_read(query: str, params: tuple = ()) -> list[tuple]:
    """Execute one explicitly read-only Turso statement with transient retry."""
    validate_read_query(query)
    return run_with_retry(lambda: _execute_read_once(query, params))
