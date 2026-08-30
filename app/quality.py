from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class QualityRubric:
    novelty: float
    hook: float
    usefulness: float
    readability: float
    tone: float
    cta: float


@dataclass(frozen=True)
class QualityDecision:
    score: float
    action: str
    reasons: tuple[str, ...] = ()


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return min(high, max(low, float(value)))


def combine_quality_score(rubric: QualityRubric, penalties: Iterable[float]) -> float:
    weighted = (
        _clamp(rubric.novelty) * 0.25
        + _clamp(rubric.hook) * 0.20
        + _clamp(rubric.usefulness) * 0.20
        + _clamp(rubric.readability) * 0.15
        + _clamp(rubric.tone) * 0.10
        + _clamp(rubric.cta) * 0.10
    )
    score = weighted + sum(float(penalty) for penalty in penalties)
    return round(_clamp(score), 2)


def decision_for_score(score: float, reasons: tuple[str, ...] = ()) -> QualityDecision:
    normalized = _clamp(score)
    if normalized >= 75.0:
        action = "publish"
    elif normalized >= 65.0:
        action = "rewrite"
    else:
        action = "reject"
    return QualityDecision(round(normalized, 2), action, reasons)
