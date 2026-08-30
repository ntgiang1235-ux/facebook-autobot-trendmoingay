from datetime import datetime, timezone

import autobot
from app import db, notifications
from app.facebook_metrics import FacebookMetricsError, collect_post_metrics
from app.http import secure_session_from
from app.metrics_repository import (
    MetricSnapshot,
    due_posts,
    load_scoring_baseline,
    save_snapshot,
)
from app.scoring import engagement_rate, score_content


def _parse_published_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_hours(now: datetime, published_at: str) -> int:
    published = _parse_published_at(published_at)
    return max(0, int((now - published).total_seconds() // 3600))


def collect_due_metrics(now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    db.ensure_schema()
    pending = due_posts(db.execute, now.isoformat())
    if not pending:
        return {"due": 0, "processed": 0, "failed": 0}

    token = (autobot.FB_ACCESS_TOKEN or "").strip()
    if not token:
        raise RuntimeError("Thiếu biến môi trường: FB_ACCESS_TOKEN")

    http = secure_session_from(autobot.http)
    baseline = load_scoring_baseline(db.execute)
    processed = 0
    failed = 0

    for post in pending:
        try:
            collected = collect_post_metrics(http, post.facebook_post_id, token)
        except FacebookMetricsError as exc:
            failed += 1
            notifications.send_failure(
                f"metrics:{post.facebook_post_id}",
                exc,
                None,
            )
            continue

        follower_available = (
            collected.follower_delta is not None
            and "follower_delta" in collected.capabilities
        )
        score = score_content(
            collected,
            baseline,
            follower_available=follower_available,
        )
        snapshot = MetricSnapshot(
            facebook_post_id=post.facebook_post_id,
            measured_at=now.isoformat(),
            age_hours=_age_hours(now, post.published_at),
            reactions=collected.reactions,
            comments=collected.comments,
            shares=collected.shares,
            reach=collected.reach,
            impressions=collected.impressions,
            video_views=collected.video_views,
            follower_delta=collected.follower_delta,
            engagement_rate=engagement_rate(collected),
            content_score=score.score,
            metric_capabilities=collected.capabilities,
            score_kind=post.score_kind,
        )
        save_snapshot(db.execute, snapshot)
        processed += 1

    if pending and processed == 0 and failed:
        raise RuntimeError(
            f"Facebook metrics không xử lý được post nào ({failed}/{len(pending)} lỗi)"
        )

    return {"due": len(pending), "processed": processed, "failed": failed}
