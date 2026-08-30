from dataclasses import dataclass
from datetime import datetime


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
