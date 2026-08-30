from dataclasses import dataclass
from datetime import datetime, timezone

from app.learning import LearningSample, aggregate_dimension
from app.learning_repository import LearningObservation, load_learning_observations
from app.style_registry import StyleVariant, list_active_styles, set_style_status


MIN_MATURE_SAMPLES = 5
PROMOTE_14D_RATIO = 1.05
PROMOTE_7D_FLOOR_RATIO = 0.95
RETIRE_14D_RATIO = 0.80
RETIRE_7D_RATIO = 0.90

_REGISTRY_TO_OBSERVED = {
    "hook": "hook_type",
    "tone": "style_type",
    "cta": "cta_type",
}


@dataclass(frozen=True)
class LifecycleResult:
    status: str
    dimension: str | None = None
    value: str | None = None
    parent_value: str | None = None
    style_id: int | None = None
    experiment_mature_samples: int = 0
    parent_mature_samples: int = 0
    experiment_score_14d: float = 0.0
    parent_score_14d: float = 0.0
    experiment_score_7d: float = 0.0
    parent_score_7d: float = 0.0
    experiment_success_rate: float = 0.0
    parent_success_rate: float = 0.0
    detail: str = ""


def _as_utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _pending_experiment(execute_fn) -> StyleVariant | None:
    pending: list[StyleVariant] = []
    for dimension in _REGISTRY_TO_OBSERVED:
        pending.extend(
            item
            for item in list_active_styles(execute_fn, dimension)
            if item.status == "explore"
        )
    if len(pending) > 1:
        raise RuntimeError("multiple pending style experiments violate lifecycle invariant")
    return pending[0] if pending else None


def _learning_samples(observations: list[LearningObservation]) -> list[LearningSample]:
    return [
        LearningSample(
            score=observation.score,
            published_at=observation.published_at,
            score_kind=observation.score_kind,
        )
        for observation in observations
    ]


def _result(
    status: str,
    experiment: StyleVariant,
    experiment_stat,
    parent_stat,
    detail: str,
) -> LifecycleResult:
    return LifecycleResult(
        status=status,
        dimension=experiment.dimension,
        value=experiment.value,
        parent_value=experiment.parent_value,
        style_id=experiment.id,
        experiment_mature_samples=experiment_stat.mature_sample_count,
        parent_mature_samples=parent_stat.mature_sample_count,
        experiment_score_14d=experiment_stat.weighted_score_14d,
        parent_score_14d=parent_stat.weighted_score_14d,
        experiment_score_7d=experiment_stat.recent_score_7d,
        parent_score_7d=parent_stat.recent_score_7d,
        experiment_success_rate=experiment_stat.success_rate,
        parent_success_rate=parent_stat.success_rate,
        detail=detail,
    )


def review_pending_experiment(execute_fn, *, now: datetime | None = None) -> LifecycleResult:
    """Deterministically keep, promote, or retire the one pending style experiment."""
    current = _as_utc(now)
    experiment = _pending_experiment(execute_fn)
    if experiment is None:
        return LifecycleResult(status="no_pending", detail="no pending style experiment")

    observed_field = _REGISTRY_TO_OBSERVED.get(experiment.dimension)
    if observed_field is None:
        raise RuntimeError(f"unsupported style experiment dimension: {experiment.dimension}")

    observations = load_learning_observations(execute_fn, now=current)
    experiment_key = f"{experiment.dimension}:{experiment.value}"
    experiment_observations = [
        observation
        for observation in observations
        if observation.style_experiment_key == experiment_key
    ]
    experiment_stat = aggregate_dimension(
        _learning_samples(experiment_observations),
        current,
    )

    empty_parent_stat = aggregate_dimension([], current)
    if experiment_stat.mature_sample_count < MIN_MATURE_SAMPLES:
        return _result(
            "insufficient_experiment_data",
            experiment,
            experiment_stat,
            empty_parent_stat,
            f"experiment has {experiment_stat.mature_sample_count} mature samples; need {MIN_MATURE_SAMPLES}",
        )

    if not experiment.parent_value:
        return _result(
            "insufficient_parent_data",
            experiment,
            experiment_stat,
            empty_parent_stat,
            "experiment has no parent value for control comparison",
        )

    parent_observations = [
        observation
        for observation in observations
        if observation.style_experiment_key is None
        and str(getattr(observation, observed_field)) == experiment.parent_value
    ]
    parent_stat = aggregate_dimension(
        _learning_samples(parent_observations),
        current,
    )
    if parent_stat.mature_sample_count < MIN_MATURE_SAMPLES:
        return _result(
            "insufficient_parent_data",
            experiment,
            experiment_stat,
            parent_stat,
            f"parent control has {parent_stat.mature_sample_count} mature samples; need {MIN_MATURE_SAMPLES}",
        )

    promote = (
        experiment_stat.weighted_score_14d
        >= parent_stat.weighted_score_14d * PROMOTE_14D_RATIO
        and experiment_stat.recent_score_7d
        >= parent_stat.recent_score_7d * PROMOTE_7D_FLOOR_RATIO
        and experiment_stat.success_rate >= parent_stat.success_rate
    )
    if promote:
        set_style_status(
            execute_fn,
            experiment.id,
            "active",
            changed_at=current.isoformat(),
        )
        return _result(
            "promoted",
            experiment,
            experiment_stat,
            parent_stat,
            "experiment outperformed parent with mature evidence",
        )

    retire = (
        experiment_stat.weighted_score_14d
        <= parent_stat.weighted_score_14d * RETIRE_14D_RATIO
        and experiment_stat.recent_score_7d
        <= parent_stat.recent_score_7d * RETIRE_7D_RATIO
    )
    if retire:
        set_style_status(
            execute_fn,
            experiment.id,
            "retired",
            changed_at=current.isoformat(),
        )
        return _result(
            "retired",
            experiment,
            experiment_stat,
            parent_stat,
            "experiment materially underperformed parent with mature evidence",
        )

    return _result(
        "kept_explore",
        experiment,
        experiment_stat,
        parent_stat,
        "mature evidence is inconclusive; keep controlled exploration",
    )
