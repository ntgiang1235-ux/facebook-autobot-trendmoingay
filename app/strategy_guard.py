from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from app.strategy_models import StrategySnapshot
from app.strategy_repository import (
    load_config,
    load_stats,
    load_strategy_version,
    save_config,
    save_strategy_version,
    upsert_stat,
)


FINAL_HORIZON_HOURS = 72
COHORT_DAYS = 7
MIN_FINAL_SAMPLES = 5
MIN_FINAL_COVERAGE = 0.80
ROLLBACK_REGRESSION = 0.20


@dataclass(frozen=True)
class StrategyEvidence:
    start_at: str
    end_at: str
    eligible_count: int
    final_count: int
    average_score: float | None
    coverage: float


@dataclass(frozen=True)
class StrategyGuardResult:
    status: str
    recent: StrategyEvidence
    prior: StrategyEvidence
    regression_ratio: float | None = None
    current_version: int | None = None
    last_good_version: int | None = None
    rollback_version: int | None = None
    detail: str = ""


def _as_utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _windows(current: datetime) -> tuple[datetime, datetime, datetime, datetime]:
    recent_end = current - timedelta(hours=FINAL_HORIZON_HOURS)
    recent_start = recent_end - timedelta(days=COHORT_DAYS)
    prior_end = recent_start
    prior_start = prior_end - timedelta(days=COHORT_DAYS)
    return prior_start, prior_end, recent_start, recent_end


def _cohort_evidence(execute_fn, start: datetime, end: datetime) -> StrategyEvidence:
    rows = execute_fn(
        """
        SELECT COUNT(*),
               SUM(CASE WHEN cm.content_score IS NOT NULL THEN 1 ELSE 0 END),
               AVG(cm.content_score)
        FROM content_posts cp
        LEFT JOIN content_metrics cm
          ON cm.facebook_post_id = cp.facebook_post_id
         AND cm.score_kind = 'final'
        WHERE cp.status = 'published'
          AND cp.facebook_post_id IS NOT NULL
          AND cp.published_at >= ?
          AND cp.published_at < ?
          AND cp.strategy_mode IN ('exploit', 'explore', 'retest')
          AND cp.strategy_version IS NOT NULL
        """,
        (start.isoformat(), end.isoformat()),
    )
    eligible = int(rows[0][0] or 0) if rows else 0
    final_count = int(rows[0][1] or 0) if rows else 0
    average = float(rows[0][2]) if rows and rows[0][2] is not None else None
    coverage = (final_count / eligible) if eligible else 0.0
    return StrategyEvidence(
        start_at=start.isoformat(),
        end_at=end.isoformat(),
        eligible_count=eligible,
        final_count=final_count,
        average_score=average,
        coverage=coverage,
    )


def _next_version_id(execute_fn) -> int:
    rows = execute_fn("SELECT COALESCE(MAX(version_id), 0) FROM strategy_versions", ())
    return int(rows[0][0]) + 1 if rows else 1


def _effective_rollback_weights(execute_fn, target: StrategySnapshot, current: datetime):
    stats = load_stats(execute_fn)
    effective: dict[str, dict[str, float]] = {}
    for stat in stats:
        target_weight = target.weights.get(stat.dimension, {}).get(stat.value, 0.0)
        restored_weight = (
            0.0 if stat.status in {"retired", "suspended"} else float(target_weight)
        )
        upsert_stat(
            execute_fn,
            replace(
                stat,
                current_weight=restored_weight,
                updated_at=current.isoformat(),
            ),
        )
        effective.setdefault(stat.dimension, {})[stat.value] = restored_weight
    return effective


def _empty_evidence(start: datetime, end: datetime) -> StrategyEvidence:
    return StrategyEvidence(
        start_at=start.isoformat(),
        end_at=end.isoformat(),
        eligible_count=0,
        final_count=0,
        average_score=None,
        coverage=0.0,
    )


def run_strategy_guard(execute_fn, *, now: datetime | None = None) -> StrategyGuardResult:
    """Promote a healthy current strategy or rollback a decisive regression."""
    current = _as_utc(now)
    prior_start, prior_end, recent_start, recent_end = _windows(current)
    config = load_config(execute_fn)

    if not config.adaptive_enabled:
        return StrategyGuardResult(
            status="disabled",
            recent=_empty_evidence(recent_start, recent_end),
            prior=_empty_evidence(prior_start, prior_end),
            current_version=config.current_strategy_version,
            last_good_version=config.last_good_strategy_version,
            detail="adaptive strategy guard disabled",
        )

    if config.current_strategy_version is None:
        return StrategyGuardResult(
            status="no_current",
            recent=_empty_evidence(recent_start, recent_end),
            prior=_empty_evidence(prior_start, prior_end),
            last_good_version=config.last_good_strategy_version,
            detail="no current adaptive strategy version",
        )

    prior = _cohort_evidence(execute_fn, prior_start, prior_end)
    recent = _cohort_evidence(execute_fn, recent_start, recent_end)

    if prior.final_count < MIN_FINAL_SAMPLES or recent.final_count < MIN_FINAL_SAMPLES:
        return StrategyGuardResult(
            status="insufficient_data",
            recent=recent,
            prior=prior,
            current_version=config.current_strategy_version,
            last_good_version=config.last_good_strategy_version,
            detail=(
                f"need {MIN_FINAL_SAMPLES} final samples per cohort: "
                f"prior={prior.final_count}, recent={recent.final_count}"
            ),
        )

    if prior.coverage < MIN_FINAL_COVERAGE or recent.coverage < MIN_FINAL_COVERAGE:
        return StrategyGuardResult(
            status="metric_degraded",
            recent=recent,
            prior=prior,
            current_version=config.current_strategy_version,
            last_good_version=config.last_good_strategy_version,
            detail=(
                f"final metric coverage below {MIN_FINAL_COVERAGE:.0%}: "
                f"prior={prior.coverage:.0%}, recent={recent.coverage:.0%}"
            ),
        )

    if prior.average_score is None or recent.average_score is None or prior.average_score <= 0:
        return StrategyGuardResult(
            status="insufficient_data",
            recent=recent,
            prior=prior,
            current_version=config.current_strategy_version,
            last_good_version=config.last_good_strategy_version,
            detail="cannot compute percentage regression from unavailable/zero prior score",
        )

    regression = 1.0 - (recent.average_score / prior.average_score)
    if regression <= ROLLBACK_REGRESSION:
        if config.last_good_strategy_version == config.current_strategy_version:
            return StrategyGuardResult(
                status="stable",
                recent=recent,
                prior=prior,
                regression_ratio=regression,
                current_version=config.current_strategy_version,
                last_good_version=config.last_good_strategy_version,
                detail=f"strategy performance healthy: regression={regression:.1%}",
            )
        promoted = replace(
            config,
            last_good_strategy_version=config.current_strategy_version,
        )
        save_config(execute_fn, promoted)
        return StrategyGuardResult(
            status="promoted_last_good",
            recent=recent,
            prior=prior,
            regression_ratio=regression,
            current_version=config.current_strategy_version,
            last_good_version=config.current_strategy_version,
            detail=f"promoted v{config.current_strategy_version} as last-good",
        )

    target_version = config.last_good_strategy_version
    if target_version is None:
        return StrategyGuardResult(
            status="no_last_good",
            recent=recent,
            prior=prior,
            regression_ratio=regression,
            current_version=config.current_strategy_version,
            detail="regression detected but no last-good strategy is available",
        )

    target = load_strategy_version(execute_fn, target_version)
    if target is None:
        return StrategyGuardResult(
            status="no_last_good",
            recent=recent,
            prior=prior,
            regression_ratio=regression,
            current_version=config.current_strategy_version,
            last_good_version=target_version,
            detail=f"last-good strategy v{target_version} snapshot is unavailable",
        )

    effective_weights = _effective_rollback_weights(execute_fn, target, current)
    rollback_version = _next_version_id(execute_fn)
    rollback_config = replace(
        config,
        current_strategy_version=rollback_version,
        last_good_strategy_version=target_version,
    )
    save_strategy_version(
        execute_fn,
        StrategySnapshot(
            version_id=rollback_version,
            weights=effective_weights,
            config=rollback_config,
            created_at=current.isoformat(),
            reason=f"automatic rollback to v{target_version}",
            is_last_good=False,
        ),
    )
    save_config(execute_fn, rollback_config)

    return StrategyGuardResult(
        status="rolled_back",
        recent=recent,
        prior=prior,
        regression_ratio=regression,
        current_version=rollback_version,
        last_good_version=target_version,
        rollback_version=rollback_version,
        detail=(
            f"automatic rollback to v{target_version}: "
            f"prior={prior.average_score:.1f}, recent={recent.average_score:.1f}, "
            f"regression={regression:.1%}"
        ),
    )
