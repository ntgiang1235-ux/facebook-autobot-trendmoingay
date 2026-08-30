import math
from dataclasses import dataclass
from statistics import median
from typing import Sequence

from app.facebook_metrics import CollectedMetrics


MIN_BASELINE_SAMPLES = 5


@dataclass(frozen=True)
class ScoringBaseline:
    engagement_rates: tuple[float, ...]
    weighted_interactions: tuple[float, ...]
    reach: tuple[float, ...]
    impressions: tuple[float, ...]
    conversation: tuple[float, ...]
    follower_delta: tuple[float, ...]


@dataclass(frozen=True)
class ScoreResult:
    score: float
    maturity: str
    components: dict[str, float]


def weighted_interactions(reactions: int, comments: int, shares: int) -> float:
    return float(reactions + 2 * comments + 3 * shares)


def _core_interactions(metrics: CollectedMetrics) -> float | None:
    if metrics.reactions is None or metrics.comments is None or metrics.shares is None:
        return None
    return weighted_interactions(metrics.reactions, metrics.comments, metrics.shares)


def _conversation(metrics: CollectedMetrics) -> float | None:
    if metrics.comments is None or metrics.shares is None:
        return None
    return float(metrics.comments + 3 * metrics.shares)


def engagement_rate(metrics: CollectedMetrics) -> float | None:
    interactions = _core_interactions(metrics)
    if interactions is None:
        return None
    denominator = None
    if metrics.reach is not None and metrics.reach > 0:
        denominator = metrics.reach
    elif metrics.impressions is not None and metrics.impressions > 0:
        denominator = metrics.impressions
    if denominator is None:
        return None
    return interactions / denominator


def _quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        raise ValueError("baseline rỗng")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def winsorize(
    value: float,
    baseline_values: Sequence[float],
    lower: float = 0.05,
    upper: float = 0.95,
) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("value phải hữu hạn")
    low = _quantile(baseline_values, lower)
    high = _quantile(baseline_values, upper)
    return min(high, max(low, numeric))


def _relative_performance(value: float | None, baseline_values: Sequence[float]) -> tuple[float, bool]:
    clean = [float(item) for item in baseline_values if math.isfinite(float(item))]
    if value is None or not math.isfinite(float(value)) or len(clean) < MIN_BASELINE_SAMPLES:
        return 50.0, False

    capped = winsorize(float(value), clean)
    middle = float(median(clean))
    low = _quantile(clean, 0.05)
    high = _quantile(clean, 0.95)

    if high <= low:
        return 50.0, True
    if capped <= middle:
        if middle <= low:
            return 50.0, True
        score = 50.0 * (capped - low) / (middle - low)
    else:
        if high <= middle:
            return 50.0, True
        score = 50.0 + 50.0 * (capped - middle) / (high - middle)
    return round(min(100.0, max(0.0, score)), 2), True


def _neutral_result(component_names: Sequence[str]) -> ScoreResult:
    return ScoreResult(
        score=50.0,
        maturity="insufficient_baseline",
        components={name: 50.0 for name in component_names},
    )


def score_content(
    metrics: CollectedMetrics,
    baseline: ScoringBaseline,
    follower_available: bool,
) -> ScoreResult:
    exposure_value: float | None = None
    exposure_baseline: Sequence[float] = ()
    if metrics.reach is not None:
        exposure_value = float(metrics.reach)
        exposure_baseline = baseline.reach
    elif metrics.impressions is not None:
        exposure_value = float(metrics.impressions)
        exposure_baseline = baseline.impressions

    interactions = _core_interactions(metrics)
    conversation_value = _conversation(metrics)

    if exposure_value is None:
        names = ("interactions", "conversation")
        if len(baseline.weighted_interactions) < MIN_BASELINE_SAMPLES or len(baseline.conversation) < MIN_BASELINE_SAMPLES:
            return _neutral_result(names)
        interaction_score, interaction_mature = _relative_performance(
            interactions, baseline.weighted_interactions
        )
        conversation_score, conversation_mature = _relative_performance(
            conversation_value, baseline.conversation
        )
        maturity = "mature" if interaction_mature and conversation_mature else "partial_metrics"
        score = 0.60 * interaction_score + 0.40 * conversation_score
        return ScoreResult(
            score=round(score, 2),
            maturity=maturity,
            components={
                "interactions": interaction_score,
                "conversation": conversation_score,
            },
        )

    use_follower = follower_available and metrics.follower_delta is not None
    component_names = ["engagement", "exposure", "conversation"]
    required_baselines = [baseline.engagement_rates, exposure_baseline, baseline.conversation]
    if use_follower:
        component_names.append("follower")
        required_baselines.append(baseline.follower_delta)
    if any(len(values) < MIN_BASELINE_SAMPLES for values in required_baselines):
        return _neutral_result(component_names)

    engagement_score, engagement_mature = _relative_performance(
        engagement_rate(metrics), baseline.engagement_rates
    )
    exposure_score, exposure_mature = _relative_performance(exposure_value, exposure_baseline)
    conversation_score, conversation_mature = _relative_performance(
        conversation_value, baseline.conversation
    )
    components = {
        "engagement": engagement_score,
        "exposure": exposure_score,
        "conversation": conversation_score,
    }

    if use_follower:
        follower_score, follower_mature = _relative_performance(
            float(metrics.follower_delta), baseline.follower_delta
        )
        components["follower"] = follower_score
        score = (
            0.35 * engagement_score
            + 0.30 * exposure_score
            + 0.20 * conversation_score
            + 0.15 * follower_score
        )
        mature = engagement_mature and exposure_mature and conversation_mature and follower_mature
    else:
        score = 0.40 * engagement_score + 0.35 * exposure_score + 0.25 * conversation_score
        mature = engagement_mature and exposure_mature and conversation_mature

    return ScoreResult(
        score=round(score, 2),
        maturity="mature" if mature else "partial_metrics",
        components=components,
    )
