import json
from dataclasses import dataclass


VALID_SCORE_KINDS = {"early", "final"}


@dataclass(frozen=True)
class MetricSnapshot:
    facebook_post_id: str
    measured_at: str
    age_hours: int
    reactions: int | None
    comments: int | None
    shares: int | None
    reach: int | None
    impressions: int | None
    video_views: int | None
    follower_delta: int | None
    engagement_rate: float | None
    content_score: float
    metric_capabilities: frozenset[str]
    score_kind: str


@dataclass(frozen=True)
class DuePost:
    content_id: int
    facebook_post_id: str
    published_at: str
    score_kind: str


def save_snapshot(execute_fn, snapshot: MetricSnapshot) -> None:
    if snapshot.score_kind not in VALID_SCORE_KINDS:
        raise ValueError(f"score_kind không hợp lệ: {snapshot.score_kind}")

    capabilities = json.dumps(sorted(snapshot.metric_capabilities), ensure_ascii=False)
    execute_fn(
        """
        INSERT INTO content_metrics (
            facebook_post_id, measured_at, age_hours, reactions, comments, shares,
            reach, impressions, video_views, follower_delta, engagement_rate,
            content_score, metric_capabilities, score_kind
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(facebook_post_id, score_kind) DO UPDATE SET
            measured_at = excluded.measured_at,
            age_hours = excluded.age_hours,
            reactions = excluded.reactions,
            comments = excluded.comments,
            shares = excluded.shares,
            reach = excluded.reach,
            impressions = excluded.impressions,
            video_views = excluded.video_views,
            follower_delta = excluded.follower_delta,
            engagement_rate = excluded.engagement_rate,
            content_score = excluded.content_score,
            metric_capabilities = excluded.metric_capabilities
        """,
        (
            snapshot.facebook_post_id,
            snapshot.measured_at,
            snapshot.age_hours,
            snapshot.reactions,
            snapshot.comments,
            snapshot.shares,
            snapshot.reach,
            snapshot.impressions,
            snapshot.video_views,
            snapshot.follower_delta,
            snapshot.engagement_rate,
            snapshot.content_score,
            capabilities,
            snapshot.score_kind,
        ),
    )


def due_posts(execute_fn, now_iso: str) -> list[DuePost]:
    rows = execute_fn(
        """
        WITH clock(now_iso) AS (VALUES (?)), eligible AS (
            SELECT cp.id,
                   cp.facebook_post_id,
                   cp.published_at,
                   (julianday(clock.now_iso) - julianday(cp.published_at)) * 24 AS age_hours
            FROM content_posts cp, clock
            WHERE cp.status = 'published'
              AND cp.facebook_post_id IS NOT NULL
              AND cp.published_at IS NOT NULL
        )
        SELECT e.id, e.facebook_post_id, e.published_at,
               CASE
                   WHEN e.age_hours >= 72 AND NOT EXISTS (
                       SELECT 1 FROM content_metrics cm
                       WHERE cm.facebook_post_id = e.facebook_post_id
                         AND cm.score_kind = 'final'
                   ) THEN 'final'
                   WHEN e.age_hours >= 24 AND e.age_hours < 72 AND NOT EXISTS (
                       SELECT 1 FROM content_metrics cm
                       WHERE cm.facebook_post_id = e.facebook_post_id
                         AND cm.score_kind = 'early'
                   ) THEN 'early'
               END AS score_kind
        FROM eligible e
        WHERE (
            e.age_hours >= 72 AND NOT EXISTS (
                SELECT 1 FROM content_metrics cm
                WHERE cm.facebook_post_id = e.facebook_post_id
                  AND cm.score_kind = 'final'
            )
        ) OR (
            e.age_hours >= 24 AND e.age_hours < 72 AND NOT EXISTS (
                SELECT 1 FROM content_metrics cm
                WHERE cm.facebook_post_id = e.facebook_post_id
                  AND cm.score_kind = 'early'
            )
        )
        ORDER BY e.published_at ASC
        """,
        (now_iso,),
    )
    return [DuePost(int(row[0]), str(row[1]), str(row[2]), str(row[3])) for row in rows]


def recent_final_scores(execute_fn, limit: int = 30) -> list[float]:
    rows = execute_fn(
        """
        SELECT content_score
        FROM content_metrics
        WHERE score_kind = 'final'
          AND content_score IS NOT NULL
        ORDER BY measured_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [float(row[0]) for row in rows if row[0] is not None]


def load_scoring_baseline(execute_fn, limit: int = 30):
    from app.scoring import ScoringBaseline, weighted_interactions

    rows = execute_fn(
        """
        SELECT reactions, comments, shares, reach, impressions, follower_delta
        FROM content_metrics
        WHERE score_kind = 'final'
        ORDER BY measured_at DESC
        LIMIT ?
        """,
        (limit,),
    )

    engagement_rates = []
    interaction_values = []
    reach_values = []
    impression_values = []
    conversation_values = []
    follower_values = []

    for reactions, comments, shares, reach, impressions, follower_delta in rows:
        if reactions is not None and comments is not None and shares is not None:
            interactions = weighted_interactions(int(reactions), int(comments), int(shares))
            interaction_values.append(interactions)
            denominator = None
            if reach is not None and float(reach) > 0:
                denominator = float(reach)
            elif impressions is not None and float(impressions) > 0:
                denominator = float(impressions)
            if denominator is not None:
                engagement_rates.append(interactions / denominator)
        if reach is not None:
            reach_values.append(float(reach))
        if impressions is not None:
            impression_values.append(float(impressions))
        if comments is not None and shares is not None:
            conversation_values.append(float(comments) + 3.0 * float(shares))
        if follower_delta is not None:
            follower_values.append(float(follower_delta))

    return ScoringBaseline(
        engagement_rates=tuple(engagement_rates),
        weighted_interactions=tuple(interaction_values),
        reach=tuple(reach_values),
        impressions=tuple(impression_values),
        conversation=tuple(conversation_values),
        follower_delta=tuple(follower_values),
    )
