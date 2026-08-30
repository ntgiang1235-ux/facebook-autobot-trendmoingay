from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
LEARNING_WINDOW_DAYS = 14


@dataclass(frozen=True)
class LearningObservation:
    facebook_post_id: str
    category: str
    time_bucket: str | None
    hook_type: str
    style_type: str
    cta_type: str
    format_type: str
    score: float
    score_kind: str
    published_at: datetime
    style_experiment_key: str | None = None


def _as_utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _time_bucket(scheduled_for: str | None) -> str | None:
    if not scheduled_for:
        return None
    return _parse_utc(scheduled_for).astimezone(VIETNAM_TZ).strftime("%H:%M")


def load_learning_observations(execute_fn, *, now: datetime | None = None) -> list[LearningObservation]:
    """Load only adaptive, scored publishes inside the approved 14-day window."""
    current = _as_utc(now)
    cutoff = current - timedelta(days=LEARNING_WINDOW_DAYS)
    rows = execute_fn(
        """
        SELECT cp.facebook_post_id, cp.category, cp.scheduled_for,
               cp.hook_type, cp.style_type, cp.cta_type, cp.format_type,
               cm.content_score, cm.score_kind, cp.published_at,
               cp.style_experiment_key
        FROM content_posts cp
        JOIN content_metrics cm
          ON cm.facebook_post_id = cp.facebook_post_id
        WHERE cp.status = 'published'
          AND cp.facebook_post_id IS NOT NULL
          AND cp.published_at IS NOT NULL
          AND cp.published_at >= ?
          AND cp.published_at <= ?
          AND cp.strategy_mode <> 'manual'
          AND cm.score_kind IN ('early', 'final')
          AND cm.content_score IS NOT NULL
        ORDER BY cp.published_at ASC, cp.facebook_post_id ASC, cm.score_kind ASC
        """,
        (cutoff.isoformat(), current.isoformat()),
    )

    observations: list[LearningObservation] = []
    for row in rows:
        observations.append(
            LearningObservation(
                facebook_post_id=str(row[0]),
                category=str(row[1]),
                time_bucket=_time_bucket(str(row[2]) if row[2] is not None else None),
                hook_type=str(row[3] or "unknown"),
                style_type=str(row[4] or "unknown"),
                cta_type=str(row[5] or "none"),
                format_type=str(row[6] or "text"),
                score=float(row[7]),
                score_kind=str(row[8]),
                published_at=_parse_utc(str(row[9])),
                style_experiment_key=(str(row[10]) if row[10] is not None else None),
            )
        )
    return observations
