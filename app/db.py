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
            detail TEXT,
            scheduled_for TEXT,
            delay_minutes INTEGER
        )
        """
    )

    columns = {row[1] for row in execute("PRAGMA table_info(job_runs)")}
    if "scheduled_for" not in columns:
        execute("ALTER TABLE job_runs ADD COLUMN scheduled_for TEXT")
    if "delay_minutes" not in columns:
        execute("ALTER TABLE job_runs ADD COLUMN delay_minutes INTEGER")

    execute(
        """
        CREATE TABLE IF NOT EXISTS content_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_key TEXT,
            facebook_post_id TEXT,
            action TEXT,
            category TEXT NOT NULL,
            topic_key TEXT NOT NULL,
            topic_text TEXT NOT NULL,
            source_url TEXT,
            source_title TEXT,
            content_text TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            hook_type TEXT NOT NULL DEFAULT 'unknown',
            style_type TEXT NOT NULL DEFAULT 'unknown',
            cta_type TEXT NOT NULL DEFAULT 'none',
            format_type TEXT NOT NULL DEFAULT 'text',
            scheduled_for TEXT,
            published_at TEXT,
            strategy_mode TEXT NOT NULL DEFAULT 'baseline',
            quality_score REAL,
            duplicate_score REAL,
            strategy_version INTEGER,
            status TEXT NOT NULL,
            detail TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    execute(
        "CREATE INDEX IF NOT EXISTS idx_content_posts_category_time "
        "ON content_posts(category, published_at)"
    )
    execute(
        "CREATE INDEX IF NOT EXISTS idx_content_posts_topic_time "
        "ON content_posts(topic_key, published_at)"
    )
    execute(
        "CREATE INDEX IF NOT EXISTS idx_content_posts_facebook_id "
        "ON content_posts(facebook_post_id)"
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS style_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dimension TEXT NOT NULL,
            value TEXT NOT NULL,
            parent_value TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            promoted_at TEXT,
            retired_at TEXT,
            UNIQUE(dimension, value)
        )
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS content_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            facebook_post_id TEXT NOT NULL,
            measured_at TEXT NOT NULL,
            age_hours INTEGER NOT NULL,
            reactions INTEGER,
            comments INTEGER,
            shares INTEGER,
            reach INTEGER,
            impressions INTEGER,
            video_views INTEGER,
            follower_delta INTEGER,
            engagement_rate REAL,
            content_score REAL NOT NULL,
            metric_capabilities TEXT NOT NULL,
            score_kind TEXT NOT NULL,
            UNIQUE(facebook_post_id, score_kind)
        )
        """
    )
    execute(
        "CREATE INDEX IF NOT EXISTS idx_content_metrics_score_kind "
        "ON content_metrics(score_kind, measured_at)"
    )
    execute(
        "CREATE INDEX IF NOT EXISTS idx_content_metrics_facebook_id "
        "ON content_metrics(facebook_post_id)"
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dimension TEXT NOT NULL,
            value TEXT NOT NULL,
            sample_count INTEGER NOT NULL DEFAULT 0,
            weighted_score_14d REAL NOT NULL DEFAULT 50.0,
            recent_score_7d REAL NOT NULL DEFAULT 50.0,
            success_rate REAL NOT NULL DEFAULT 0.0,
            current_weight REAL NOT NULL DEFAULT 1.0,
            last_used_at TEXT,
            status TEXT NOT NULL DEFAULT 'insufficient_data',
            cooldown_until TEXT,
            retest_after TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(dimension, value)
        )
        """
    )
    execute(
        "CREATE INDEX IF NOT EXISTS idx_strategy_stats_dimension_status "
        "ON strategy_stats(dimension, status)"
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS adaptive_config (
            id INTEGER PRIMARY KEY,
            adaptive_enabled INTEGER NOT NULL DEFAULT 1,
            auto_schedule_enabled INTEGER NOT NULL DEFAULT 1,
            auto_suspend_enabled INTEGER NOT NULL DEFAULT 1,
            exploration_rate REAL NOT NULL DEFAULT 0.20,
            baseline_daily_volume INTEGER NOT NULL DEFAULT 12,
            current_strategy_version INTEGER,
            last_good_strategy_version INTEGER,
            CHECK(id = 1)
        )
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_versions (
            version_id INTEGER PRIMARY KEY,
            weights_json TEXT NOT NULL,
            config_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            reason TEXT NOT NULL,
            is_last_good INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    execute(
        "CREATE INDEX IF NOT EXISTS idx_strategy_versions_created_at "
        "ON strategy_versions(created_at)"
    )


def record_job(
    run_key: str,
    action: str,
    status: str,
    started_at: str,
    finished_at: str | None = None,
    detail: str = "",
    scheduled_for: str | None = None,
    delay_minutes: int | None = None,
) -> None:
    execute(
        """
        INSERT INTO job_runs (
            run_key, action, status, started_at, finished_at, detail,
            scheduled_for, delay_minutes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_key) DO UPDATE SET
            action = excluded.action,
            status = excluded.status,
            started_at = excluded.started_at,
            finished_at = excluded.finished_at,
            detail = excluded.detail,
            scheduled_for = excluded.scheduled_for,
            delay_minutes = excluded.delay_minutes
        """,
        (
            run_key,
            action,
            status,
            started_at,
            finished_at,
            detail,
            scheduled_for,
            delay_minutes,
        ),
    )
