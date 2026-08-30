import random
from dataclasses import dataclass, replace

from app.content_models import ContentCandidate
from app.content_repository import content_hash
from app.selection import (
    exploration_probabilities,
    select_mode,
    weighted_choice,
)
from app.strategy_models import StrategyStat
from app.strategy_repository import load_config, load_stats
from app.style_registry import ensure_seed_styles, list_active_styles


MIN_MATURE_SAMPLES = 5
_DIMENSIONS = (
    ("hook_type", "hook"),
    ("style_type", "tone"),
    ("cta_type", "cta"),
)
_REGISTRY_TO_STAT = {registry: stat for stat, registry in _DIMENSIONS}


@dataclass(frozen=True)
class StyleTarget:
    hook_type: str | None
    style_type: str | None
    cta_type: str | None
    mode: str
    experiment_key: str | None = None


def _mature_active(stats: list[StrategyStat], dimension: str) -> list[StrategyStat]:
    return [
        stat
        for stat in stats
        if stat.dimension == dimension
        and stat.status == "active"
        and stat.sample_count >= MIN_MATURE_SAMPLES
        and stat.current_weight > 0
    ]


def _weighted_mature_choice(stats: list[StrategyStat], dimension: str, rng) -> str | None:
    mature = _mature_active(stats, dimension)
    if not mature:
        return None
    return weighted_choice(
        [(stat.value, max(0.0, float(stat.current_weight))) for stat in mature],
        rng,
    )


def _explore_choice_from_registered(
    stats: list[StrategyStat],
    stat_dimension: str,
    registered,
    rng,
) -> str | None:
    values = [item.value for item in registered if item.status != "retired"]
    if not values:
        return _weighted_mature_choice(stats, stat_dimension, rng)

    dimension_stats = [stat for stat in stats if stat.dimension == stat_dimension]
    probabilities = exploration_probabilities(dimension_stats, values)
    if probabilities:
        return weighted_choice(list(probabilities.items()), rng)
    return _weighted_mature_choice(stats, stat_dimension, rng)


def _registered_by_dimension(execute_fn):
    return {
        registry_dimension: list_active_styles(execute_fn, registry_dimension)
        for _, registry_dimension in _DIMENSIONS
    }


def _pending_experiments(registered_by_dimension):
    pending = []
    for _, registry_dimension in _DIMENSIONS:
        for item in registered_by_dimension[registry_dimension]:
            if item.status == "explore":
                pending.append((registry_dimension, item))
    return pending


def _experiment_target(stats, registered_by_dimension, rng) -> StyleTarget | None:
    pending = _pending_experiments(registered_by_dimension)
    if not pending:
        return None

    option_keys = [
        (f"{dimension}:{item.value}", 1.0)
        for dimension, item in pending
    ]
    chosen_key = weighted_choice(option_keys, rng)
    chosen_dimension, chosen_value = chosen_key.split(":", 1)
    chosen_stat_dimension = _REGISTRY_TO_STAT[chosen_dimension]

    values = {
        stat_dimension: (
            chosen_value
            if stat_dimension == chosen_stat_dimension
            else _weighted_mature_choice(stats, stat_dimension, rng)
        )
        for stat_dimension, _ in _DIMENSIONS
    }
    return StyleTarget(
        hook_type=values["hook_type"],
        style_type=values["style_type"],
        cta_type=values["cta_type"],
        mode="explore",
        experiment_key=chosen_key,
    )


def select_style_target(execute_fn, rng=None) -> StyleTarget | None:
    """Select a mature profile, exposing at most one controlled experiment.

    One exploit/explore mode applies to the whole profile. Exploitation only uses
    mature active strategy values. During exploration, a pending custom experiment
    gets priority and changes exactly one dimension; the other dimensions use
    mature winners as controls. If no custom experiment is pending, exploration
    falls back to approved registered under-sampled values as before.
    """
    config = load_config(execute_fn)
    if not config.adaptive_enabled:
        return None

    stats = load_stats(execute_fn)
    has_mature_style = any(
        _mature_active(stats, dimension)
        for dimension, _ in _DIMENSIONS
    )
    if not has_mature_style:
        return None

    ensure_seed_styles(execute_fn)
    generator = rng or random.SystemRandom()
    mode = select_mode(generator, config.exploration_rate)

    if mode == "explore":
        registered_by_dimension = _registered_by_dimension(execute_fn)
        experiment = _experiment_target(stats, registered_by_dimension, generator)
        if experiment is not None:
            return experiment

        values = {
            stat_dimension: _explore_choice_from_registered(
                stats,
                stat_dimension,
                registered_by_dimension[registry_dimension],
                generator,
            )
            for stat_dimension, registry_dimension in _DIMENSIONS
        }
    else:
        values = {
            stat_dimension: _weighted_mature_choice(stats, stat_dimension, generator)
            for stat_dimension, _ in _DIMENSIONS
        }

    if not any(values.values()):
        return None
    return StyleTarget(
        hook_type=values["hook_type"],
        style_type=values["style_type"],
        cta_type=values["cta_type"],
        mode=mode,
    )


def restyle_candidate(candidate: ContentCandidate, target: StyleTarget, gemini_fn) -> ContentCandidate:
    """Restyle once while preserving facts and separately track experiment exposure."""
    requirements = []
    if target.hook_type:
        requirements.append(f"Hook type: {target.hook_type}")
    if target.style_type:
        requirements.append(f"Writing style/tone: {target.style_type}")
    if target.cta_type:
        requirements.append(f"CTA type: {target.cta_type}")
    if not requirements:
        return candidate

    prompt = (
        "Restyle this Facebook post to match the requested writing profile. "
        "Preserve every factual claim, source claim, URL, hashtag, important number, "
        "person/place name, and the original meaning. Do not invent facts. "
        "Do not add unsupported claims. Return only the rewritten post text, no explanation.\n"
        + "\n".join(requirements)
        + f"\nCategory: {candidate.category}\nOriginal post:\n{candidate.content_text}"
    )
    try:
        rewritten = gemini_fn(prompt)
    except Exception:
        return candidate
    if not isinstance(rewritten, str) or not rewritten.strip():
        return candidate

    text = rewritten.strip()
    return replace(
        candidate,
        topic_key=content_hash(candidate.source_url or text),
        topic_text=text,
        content_text=text,
        style_experiment_key=target.experiment_key,
    )
