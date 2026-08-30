from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median

from app.strategy_models import StrategyStat


@dataclass(frozen=True)
class LearningSample:
    score: float
    published_at: datetime
    score_kind: str


@dataclass(frozen=True)
class LearningStat:
    weighted_score_14d: float
    recent_score_7d: float
    mature_sample_count: int
    included_sample_count: int
    success_rate: float


@dataclass(frozen=True)
class WeightProposal:
    proposed_weight: float
    status: str
    reason: str


@dataclass(frozen=True)
class CategoryDecision:
    status: str
    normal_allocation_eligible: bool
    retest_eligible: bool
    proposed_weight: float
    retest_after: datetime | None
    reason: str


def recency_weight(age_days: float) -> float:
    if age_days < 0:
        return 0.0
    if age_days <= 3:
        return 1.50
    if age_days <= 7:
        return 1.25
    if age_days <= 14:
        return 1.00
    return 0.0


def _weighted_mean(weighted_values: list[tuple[float, float]], neutral: float = 50.0) -> float:
    total_weight = sum(weight for _, weight in weighted_values)
    if total_weight <= 0:
        return neutral
    return sum(value * weight for value, weight in weighted_values) / total_weight


def aggregate_dimension(
    samples: list[LearningSample],
    now: datetime,
    success_baseline: float = 50.0,
) -> LearningStat:
    included: list[tuple[LearningSample, float, float]] = []
    mature_count = 0

    for sample in samples:
        if sample.score_kind not in {"early", "final"}:
            continue
        delta = now - sample.published_at
        age_days = delta.total_seconds() / 86400.0
        base_weight = recency_weight(age_days)
        if base_weight <= 0:
            continue

        maturity_multiplier = 1.0 if sample.score_kind == "final" else 0.5
        effective_weight = base_weight * maturity_multiplier
        included.append((sample, age_days, effective_weight))
        if sample.score_kind == "final":
            mature_count += 1

    all_values = [(sample.score, weight) for sample, _, weight in included]
    recent_values = [
        (sample.score, weight)
        for sample, age_days, weight in included
        if age_days <= 7
    ]
    total_weight = sum(weight for _, _, weight in included)
    success_weight = sum(
        weight
        for sample, _, weight in included
        if sample.score >= success_baseline
    )

    return LearningStat(
        weighted_score_14d=_weighted_mean(all_values),
        recent_score_7d=_weighted_mean(recent_values),
        mature_sample_count=mature_count,
        included_sample_count=len(included),
        success_rate=(success_weight / total_weight) if total_weight > 0 else 0.0,
    )


def propose_weight(
    current_weight: float,
    score: float,
    peer_scores: list[float],
    mature_samples: int,
    min_samples: int = 5,
    floor: float = 0.01,
) -> WeightProposal:
    current = max(float(current_weight), floor)
    if mature_samples < min_samples:
        return WeightProposal(current, "insufficient_data", "fewer than 5 mature samples")
    if not peer_scores:
        return WeightProposal(current, "stable", "no peer baseline")

    peers = [float(value) for value in peer_scores]
    peer_median = float(median(peers))
    if max(peers) - min(peers) <= 1e-9 and abs(float(score) - peer_median) <= 1e-9:
        return WeightProposal(current, "stable", "peer scores are equal")

    relative = (float(score) - peer_median) / max(abs(peer_median), 1.0)
    bounded_delta = max(-0.20, min(0.20, relative))
    if abs(bounded_delta) <= 1e-12:
        return WeightProposal(current, "stable", "score matches peer median")

    lower = max(floor, current * 0.80)
    upper = max(lower, current * 1.20)
    proposed = current * (1.0 + bounded_delta)
    proposed = max(lower, min(upper, proposed))
    return WeightProposal(proposed, "adjusted", "bounded relative performance update")


def normalize_bounded_weights(
    current_weights: dict[str, float],
    proposed_weights: dict[str, float],
    active_values: set[str] | None = None,
    floor: float = 0.01,
) -> dict[str, float]:
    keys = list(current_weights)
    active = set(keys) if active_values is None else set(active_values) & set(keys)
    result = {key: 0.0 for key in keys}
    if not active:
        return result

    lower: dict[str, float] = {}
    upper: dict[str, float] = {}
    working: dict[str, float] = {}
    for key in active:
        current = max(float(current_weights[key]), 0.0)
        lower[key] = max(floor, current * 0.80)
        upper[key] = max(lower[key], current * 1.20)
        proposed = float(proposed_weights.get(key, current))
        working[key] = max(lower[key], min(upper[key], proposed))

    lower_sum = sum(lower.values())
    upper_sum = sum(upper.values())
    if lower_sum > 1.0 + 1e-12 or upper_sum < 1.0 - 1e-12:
        # Suspension/activation can make the daily movement bounds infeasible.
        # In that state normalize only the active pool; inactive values remain zero.
        total = sum(max(working[key], floor) for key in active)
        if total <= 0:
            equal = 1.0 / len(active)
            for key in active:
                result[key] = equal
            return result
        for key in active:
            result[key] = max(working[key], floor) / total
        return result

    total = sum(working.values())
    residual = 1.0 - total
    if residual > 1e-12:
        headroom = {key: upper[key] - working[key] for key in active}
        capacity = sum(headroom.values())
        if capacity > 0:
            for key in active:
                working[key] += residual * (headroom[key] / capacity)
    elif residual < -1e-12:
        excess = -residual
        reducible = {key: working[key] - lower[key] for key in active}
        capacity = sum(reducible.values())
        if capacity > 0:
            for key in active:
                working[key] -= excess * (reducible[key] / capacity)

    residue = 1.0 - sum(working.values())
    if abs(residue) > 1e-12:
        for key in active:
            candidate = working[key] + residue
            if lower[key] - 1e-12 <= candidate <= upper[key] + 1e-12:
                working[key] = candidate
                break

    for key in active:
        result[key] = working[key]
    return result


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _is_persistently_weak(stat: StrategyStat, peer_median: float) -> bool:
    threshold = float(peer_median) * 0.75
    return (
        stat.weighted_score_14d < threshold
        and stat.recent_score_7d < threshold
        and stat.success_rate < 0.40
    )


def evaluate_category_status(
    stat: StrategyStat,
    peer_median: float,
    now: datetime,
    auto_suspend_enabled: bool,
    min_samples: int = 5,
    suspension_days: int = 7,
) -> CategoryDecision:
    if stat.status == "suspended":
        retest_after = _parse_iso(stat.retest_after)
        if retest_after is None:
            retest_after = now + timedelta(days=suspension_days)
            return CategoryDecision(
                "suspended", False, False, 0.0, retest_after,
                "missing retest date renewed conservatively",
            )

        if now < retest_after:
            return CategoryDecision(
                "suspended", False, False, 0.0, retest_after,
                "suspension window still active",
            )

        last_used_at = _parse_iso(stat.last_used_at)
        retest_completed = last_used_at is not None and last_used_at >= retest_after
        if not retest_completed:
            return CategoryDecision(
                "suspended", False, True, 0.0, retest_after,
                "controlled retest is due",
            )

        recovered = (
            stat.recent_score_7d >= float(peer_median) * 0.90
            and stat.success_rate >= 0.50
        )
        if recovered:
            return CategoryDecision(
                "active", True, False, 0.05, None,
                "completed retest recovered category",
            )

        renewed = now + timedelta(days=suspension_days)
        return CategoryDecision(
            "suspended", False, False, 0.0, renewed,
            "completed retest remained weak",
        )

    if stat.sample_count < min_samples:
        return CategoryDecision(
            "insufficient_data", True, False, stat.current_weight, None,
            "fewer than 5 mature samples",
        )

    if not auto_suspend_enabled:
        return CategoryDecision(
            "active", True, False, stat.current_weight, None,
            "automatic suspension disabled",
        )

    if _is_persistently_weak(stat, peer_median):
        return CategoryDecision(
            "suspended", False, False, 0.0,
            now + timedelta(days=suspension_days),
            "persistent weakness across 14d and 7d windows",
        )

    return CategoryDecision(
        "active", True, False, stat.current_weight, None,
        "category remains eligible",
    )
