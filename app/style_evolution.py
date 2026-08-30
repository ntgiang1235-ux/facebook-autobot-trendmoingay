import re
from dataclasses import dataclass

from app.strategy_repository import load_stats
from app.style_registry import list_active_styles, register_experiment


MIN_MATURE_SAMPLES = 5
_STAT_TO_REGISTRY = {
    "hook_type": "hook",
    "style_type": "tone",
    "cta_type": "cta",
}
_VARIANT_RE = re.compile(r"^[a-z][a-z0-9]{0,15}(?:_[a-z0-9]{1,16}){1,4}$")


@dataclass(frozen=True)
class EvolutionResult:
    status: str
    dimension: str | None = None
    value: str | None = None
    parent_value: str | None = None
    style_id: int | None = None
    detail: str = ""


def _registry_snapshot(execute_fn):
    items = []
    for dimension in ("hook", "tone", "cta"):
        items.extend(list_active_styles(execute_fn, dimension))
    return items


def _eligible_parents(stats):
    return [
        stat
        for stat in stats
        if stat.dimension in _STAT_TO_REGISTRY
        and stat.status == "active"
        and stat.sample_count >= MIN_MATURE_SAMPLES
        and stat.current_weight > 0
    ]


def _best_parent(stats):
    eligible = _eligible_parents(stats)
    if not eligible:
        return None
    return sorted(
        eligible,
        key=lambda stat: (
            -float(stat.weighted_score_14d),
            stat.dimension,
            stat.value,
        ),
    )[0]


def _variant_token(raw: object, parent_value: str) -> str | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip().strip("`").strip()
    if not value or value == parent_value:
        return None
    if len(value) > 48 or not _VARIANT_RE.fullmatch(value):
        return None
    return value


def generate_next_experiment(execute_fn, gemini_fn) -> EvolutionResult:
    """Create at most one bounded style experiment from a mature winning parent."""
    registry = _registry_snapshot(execute_fn)
    pending = next((item for item in registry if item.status == "explore"), None)
    if pending is not None:
        return EvolutionResult(
            status="pending_existing",
            dimension=pending.dimension,
            value=pending.value,
            parent_value=pending.parent_value,
            style_id=pending.id,
            detail="an explore experiment is already active",
        )

    parent = _best_parent(load_stats(execute_fn))
    if parent is None:
        return EvolutionResult(
            status="insufficient_data",
            detail="no mature active style parent is available",
        )

    registry_dimension = _STAT_TO_REGISTRY[parent.dimension]
    prompt = (
        "Create exactly one controlled Facebook writing-style variant derived from "
        f"the proven {registry_dimension} style '{parent.value}'. "
        "Return ONLY a lowercase snake_case token of 2 to 5 short words. "
        "The variant must be meaningfully distinct but remain safe, readable, and suitable "
        "for a general Facebook page. Do not return prose, punctuation, URLs, hashtags, "
        "instructions, or explanations."
    )
    try:
        raw = gemini_fn(prompt)
    except Exception as error:
        return EvolutionResult(
            status="generation_unavailable",
            dimension=registry_dimension,
            parent_value=parent.value,
            detail=f"variant generation failed: {error}",
        )

    value = _variant_token(raw, parent.value)
    if value is None:
        return EvolutionResult(
            status="invalid_variant",
            dimension=registry_dimension,
            parent_value=parent.value,
            detail="generated variant token was invalid",
        )

    same_dimension_values = {
        item.value for item in registry if item.dimension == registry_dimension
    }
    if value in same_dimension_values:
        return EvolutionResult(
            status="duplicate_variant",
            dimension=registry_dimension,
            value=value,
            parent_value=parent.value,
            detail="generated variant already exists in the registry",
        )

    style_id = register_experiment(
        execute_fn,
        registry_dimension,
        value,
        parent.value,
    )
    if style_id is None:
        return EvolutionResult(
            status="registry_write_failed",
            dimension=registry_dimension,
            value=value,
            parent_value=parent.value,
            detail="experiment registry write returned no id",
        )

    return EvolutionResult(
        status="created",
        dimension=registry_dimension,
        value=value,
        parent_value=parent.value,
        style_id=style_id,
        detail="controlled style experiment created",
    )
