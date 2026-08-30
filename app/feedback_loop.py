from dataclasses import dataclass, replace
from datetime import datetime, timezone
from statistics import median

from app.learning import (
    LearningSample,
    aggregate_dimension,
    evaluate_category_status,
    normalize_bounded_weights,
    propose_weight,
)
from app.learning_repository import LearningObservation, load_learning_observations
from app.planner import BASELINE_SLOTS, CORE_ACTIONS, SAFE_TIME_BUCKETS, VIETNAM_TZ
from app.strategy_models import StrategySnapshot, StrategyStat
from app.strategy_repository import (
    load_config,
    load_stats,
    save_config,
    save_strategy_version,
    upsert_stat,
)


MIN_MATURE_SAMPLES = 5
SKIP_VALUES = {
    "hook_type": {"", "unknown"},
    "style_type": {"", "unknown"},
    "cta_type": {"", "unknown", "none"},
    "format_type": {"", "unknown"},
}


@dataclass(frozen=True)
class StrategyRefreshResult:
    version_id: int
    observation_count: int
    updated_stat_count: int


def _as_utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _latest_version(execute_fn) -> tuple[int, datetime] | None:
    rows = execute_fn(
        """
        SELECT version_id, created_at
        FROM strategy_versions
        ORDER BY version_id DESC
        LIMIT 1
        """,
        (),
    )
    if not rows:
        return None

    version_id = int(rows[0][0])
    created_at = datetime.fromisoformat(str(rows[0][1]))
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return version_id, created_at.astimezone(timezone.utc)


def _baseline_category_weights() -> dict[str, float]:
    counts = {value: 0 for value in CORE_ACTIONS}
    for _, action in BASELINE_SLOTS:
        if action in counts:
            counts[action] += 1
    total = sum(counts.values()) or 1
    return {value: counts[value] / total for value in CORE_ACTIONS}


def _next_version_id(execute_fn) -> int:
    rows = execute_fn("SELECT COALESCE(MAX(version_id), 0) FROM strategy_versions", ())
    return int(rows[0][0]) + 1 if rows else 1


def _dimension_value(observation: LearningObservation, dimension: str) -> str | None:
    if dimension == "category":
        return observation.category
    if dimension == "time_bucket":
        return observation.time_bucket
    return str(getattr(observation, dimension))


def _included_values(observations: list[LearningObservation], dimension: str) -> list[str]:
    if dimension == "category":
        return list(CORE_ACTIONS)

    values = set()
    blocked = SKIP_VALUES.get(dimension, set())
    for observation in observations:
        value = _dimension_value(observation, dimension)
        if value is None or value in blocked:
            continue
        if dimension == "time_bucket" and value not in SAFE_TIME_BUCKETS:
            continue
        values.add(value)
    return sorted(values)


def _samples_for(
    observations: list[LearningObservation], dimension: str, value: str
) -> tuple[list[LearningSample], str | None]:
    samples: list[LearningSample] = []
    last_used: datetime | None = None
    for observation in observations:
        if _dimension_value(observation, dimension) != value:
            continue
        samples.append(
            LearningSample(
                score=observation.score,
                published_at=observation.published_at,
                score_kind=observation.score_kind,
            )
        )
        if last_used is None or observation.published_at > last_used:
            last_used = observation.published_at
    return samples, last_used.isoformat() if last_used is not None else None


def _default_weights(dimension: str, values: list[str]) -> dict[str, float]:
    if dimension == "category":
        return _baseline_category_weights()
    if not values:
        return {}
    equal = 1.0 / len(values)
    return {value: equal for value in values}


def _build_raw_stats(
    observations: list[LearningObservation],
    dimension: str,
    values: list[str],
    existing: dict[tuple[str, str], StrategyStat],
    current: datetime,
) -> list[StrategyStat]:
    defaults = _default_weights(dimension, values)
    rows: list[StrategyStat] = []
    for value in values:
        previous = existing.get((dimension, value))
        samples, last_used = _samples_for(observations, dimension, value)
        aggregate = aggregate_dimension(samples, current)
        current_weight = (
            previous.current_weight if previous is not None else defaults.get(value, 0.0)
        )
        prior_status = previous.status if previous is not None else "insufficient_data"
        rows.append(
            StrategyStat(
                dimension=dimension,
                value=value,
                sample_count=aggregate.mature_sample_count,
                weighted_score_14d=aggregate.weighted_score_14d,
                recent_score_7d=aggregate.recent_score_7d,
                success_rate=aggregate.success_rate,
                current_weight=current_weight,
                last_used_at=last_used or (previous.last_used_at if previous else None),
                status=prior_status,
                cooldown_until=previous.cooldown_until if previous else None,
                retest_after=previous.retest_after if previous else None,
                updated_at=current.isoformat(),
            )
        )
    return rows


def _category_stats(
    raw_stats: list[StrategyStat],
    config,
    current: datetime,
) -> list[StrategyStat]:
    peer_scores = [stat.weighted_score_14d for stat in raw_stats]
    peer_median = float(median(peer_scores)) if peer_scores else 50.0
    current_weights = {stat.value: stat.current_weight for stat in raw_stats}
    proposals: dict[str, float] = {}
    decisions = {}

    for stat in raw_stats:
        decision = evaluate_category_status(
            stat,
            peer_median,
            current,
            config.auto_suspend_enabled,
        )
        decisions[stat.value] = decision
        if decision.status == "suspended":
            proposals[stat.value] = 0.0
            continue
        if decision.status == "active" and decision.proposed_weight != stat.current_weight:
            proposals[stat.value] = decision.proposed_weight
            continue
        peer_values = [
            candidate.weighted_score_14d
            for candidate in raw_stats
            if candidate.value != stat.value
        ]
        proposals[stat.value] = propose_weight(
            stat.current_weight,
            stat.weighted_score_14d,
            peer_values,
            stat.sample_count,
        ).proposed_weight

    active_values = {
        stat.value
        for stat in raw_stats
        if decisions[stat.value].status != "suspended"
        and stat.status != "retired"
    }
    normalized = normalize_bounded_weights(
        current_weights,
        proposals,
        active_values=active_values,
    )

    result = []
    for stat in raw_stats:
        decision = decisions[stat.value]
        status = decision.status
        if stat.status == "retired":
            status = "retired"
        result.append(
            replace(
                stat,
                current_weight=0.0 if status in {"suspended", "retired"} else normalized[stat.value],
                status=status,
                retest_after=(
                    decision.retest_after.isoformat()
                    if decision.retest_after is not None
                    else None
                ),
            )
        )
    return result


def _regular_dimension_stats(raw_stats: list[StrategyStat]) -> list[StrategyStat]:
    if not raw_stats:
        return []
    current_weights = {stat.value: stat.current_weight for stat in raw_stats}
    proposals = {}
    active_values = set()
    statuses = {}

    for stat in raw_stats:
        if stat.status in {"retired", "suspended"}:
            statuses[stat.value] = stat.status
            proposals[stat.value] = 0.0
            continue
        status = "active" if stat.sample_count >= MIN_MATURE_SAMPLES else "insufficient_data"
        statuses[stat.value] = status
        active_values.add(stat.value)
        peer_values = [
            candidate.weighted_score_14d
            for candidate in raw_stats
            if candidate.value != stat.value
        ]
        proposals[stat.value] = propose_weight(
            stat.current_weight,
            stat.weighted_score_14d,
            peer_values,
            stat.sample_count,
        ).proposed_weight

    normalized = normalize_bounded_weights(
        current_weights,
        proposals,
        active_values=active_values,
    )
    return [
        replace(
            stat,
            current_weight=(normalized[stat.value] if stat.value in active_values else 0.0),
            status=statuses[stat.value],
        )
        for stat in raw_stats
    ]


def refresh_strategy(execute_fn, *, now: datetime | None = None) -> StrategyRefreshResult:
    """Refresh strategy stats at most once per Vietnam calendar day."""
    current = _as_utc(now)
    config = load_config(execute_fn)
    latest = _latest_version(execute_fn)
    if latest is not None:
        latest_version_id, latest_created_at = latest
        if latest_created_at.astimezone(VIETNAM_TZ).date() == current.astimezone(VIETNAM_TZ).date():
            if (
                config.current_strategy_version != latest_version_id
                or config.last_good_strategy_version != latest_version_id
            ):
                save_config(
                    execute_fn,
                    replace(
                        config,
                        current_strategy_version=latest_version_id,
                        last_good_strategy_version=latest_version_id,
                    ),
                )
            return StrategyRefreshResult(
                version_id=latest_version_id,
                observation_count=0,
                updated_stat_count=0,
            )

    observations = load_learning_observations(execute_fn, now=current)
    existing_stats = {
        (stat.dimension, stat.value): stat for stat in load_stats(execute_fn)
    }

    dimensions = (
        "category",
        "time_bucket",
        "format_type",
        "hook_type",
        "style_type",
        "cta_type",
    )
    refreshed: list[StrategyStat] = []
    for dimension in dimensions:
        values = _included_values(observations, dimension)
        raw = _build_raw_stats(
            observations,
            dimension,
            values,
            existing_stats,
            current,
        )
        if dimension == "category":
            refreshed.extend(_category_stats(raw, config, current))
        else:
            refreshed.extend(_regular_dimension_stats(raw))

    for stat in refreshed:
        upsert_stat(execute_fn, stat)

    version_id = _next_version_id(execute_fn)
    updated_config = replace(
        config,
        current_strategy_version=version_id,
        last_good_strategy_version=version_id,
    )
    weights: dict[str, dict[str, float]] = {}
    for stat in refreshed:
        weights.setdefault(stat.dimension, {})[stat.value] = stat.current_weight

    snapshot = StrategySnapshot(
        version_id=version_id,
        weights=weights,
        config=updated_config,
        created_at=current.isoformat(),
        reason="14-day adaptive feedback refresh",
        is_last_good=True,
    )
    save_strategy_version(execute_fn, snapshot)
    save_config(execute_fn, updated_config)

    return StrategyRefreshResult(
        version_id=version_id,
        observation_count=len(observations),
        updated_stat_count=len(refreshed),
    )
