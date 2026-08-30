import hashlib
from datetime import datetime, timezone

from app.content_models import ContentCandidate, RecentContent


def content_hash(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _has_experiment_column(execute_fn) -> bool:
    try:
        rows = execute_fn("PRAGMA table_info(content_posts)", ())
    except Exception:
        return False
    return any(len(row) > 1 and str(row[1]) == "style_experiment_key" for row in rows)


def record_candidate(
    execute_fn,
    candidate: ContentCandidate,
    *,
    run_key: str | None = None,
    status: str = "generated",
    **metadata,
) -> int | None:
    created_at = metadata.get("created_at") or datetime.now(timezone.utc).isoformat()
    common = (
        run_key,
        metadata.get("facebook_post_id"),
        metadata.get("action"),
        candidate.category,
        candidate.topic_key,
        candidate.topic_text,
        candidate.source_url,
        candidate.source_title,
        candidate.content_text,
        content_hash(candidate.content_text),
        candidate.hook_type,
        candidate.style_type,
        candidate.cta_type,
        candidate.format_type,
    )
    tail = (
        metadata.get("scheduled_for"),
        metadata.get("published_at"),
        metadata.get("strategy_mode", "baseline"),
        metadata.get("quality_score"),
        metadata.get("duplicate_score"),
        metadata.get("strategy_version"),
        status,
        metadata.get("detail"),
        created_at,
    )

    has_experiment_column = _has_experiment_column(execute_fn)
    if candidate.style_experiment_key is not None and not has_experiment_column:
        raise RuntimeError("content_posts schema missing style_experiment_key")

    if has_experiment_column:
        query = """
        INSERT INTO content_posts (
            run_key, facebook_post_id, action, category, topic_key, topic_text,
            source_url, source_title, content_text, content_hash, hook_type,
            style_type, cta_type, format_type, style_experiment_key,
            scheduled_for, published_at, strategy_mode, quality_score,
            duplicate_score, strategy_version, status, detail, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """
        params = common + (candidate.style_experiment_key,) + tail
    else:
        # Transitional compatibility for direct callers/tests that have not run
        # ensure_schema yet. Experiment exposure itself is never silently lost.
        query = """
        INSERT INTO content_posts (
            run_key, facebook_post_id, action, category, topic_key, topic_text,
            source_url, source_title, content_text, content_hash, hook_type,
            style_type, cta_type, format_type, scheduled_for, published_at,
            strategy_mode, quality_score, duplicate_score, strategy_version,
            status, detail, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """
        params = common + tail

    rows = execute_fn(query, params)
    if not rows:
        return None
    return int(rows[0][0])


def recent_content(
    execute_fn,
    category: str,
    since_iso: str,
    limit: int = 30,
) -> list[RecentContent]:
    rows = execute_fn(
        """
        SELECT id, category, topic_key, topic_text, content_text, source_url, published_at
        FROM content_posts
        WHERE category = ?
          AND status = 'published'
          AND published_at IS NOT NULL
          AND published_at >= ?
        ORDER BY published_at DESC
        LIMIT ?
        """,
        (category, since_iso, limit),
    )
    return [RecentContent(*row) for row in rows]


def mark_published(
    execute_fn,
    content_id: int,
    facebook_post_id: str,
    published_at: str,
) -> None:
    execute_fn(
        """
        UPDATE content_posts
        SET facebook_post_id = ?, published_at = ?, status = 'published', detail = NULL
        WHERE id = ?
        """,
        (facebook_post_id, published_at, content_id),
    )


def mark_rejected(
    execute_fn,
    content_id: int,
    detail: str,
    duplicate_score: float | None = None,
) -> None:
    execute_fn(
        """
        UPDATE content_posts
        SET status = 'rejected', detail = ?, duplicate_score = ?
        WHERE id = ?
        """,
        (detail, duplicate_score, content_id),
    )
