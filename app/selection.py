from dataclasses import dataclass

from app.strategy_models import StrategyStat


@dataclass(frozen=True)
class Selection:
    value: str
    mode: str
    reason: str


def select_mode(rng, exploration_rate: float = 0.20) -> str:
    rate = max(0.0, min(1.0, float(exploration_rate)))
    return "explore" if rng.random() < rate else "exploit"


def weighted_choice(options: list[tuple[str, float]], rng) -> str:
    if not options:
        raise ValueError("weighted_choice requires at least one option")

    weights = [max(0.0, float(weight)) for _, weight in options]
    total = sum(weights)
    if total <= 0:
        weights = [1.0 for _ in options]
        total = float(len(options))

    target = rng.random() * total
    cumulative = 0.0
    for (value, _), weight in zip(options, weights):
        cumulative += weight
        if target < cumulative:
            return value
    return options[-1][0]


def _normalize(raw: dict[str, float]) -> dict[str, float]:
    if not raw:
        return {}
    total = sum(max(0.0, float(weight)) for weight in raw.values())
    if total <= 0:
        equal = 1.0 / len(raw)
        return {value: equal for value in raw}
    return {
        value: max(0.0, float(weight)) / total
        for value, weight in raw.items()
    }


def _eligible_for_selection(stat: StrategyStat) -> bool:
    return stat.status not in {"suspended", "retired"}


def exploit_probabilities(
    dimension_stats: list[StrategyStat],
    floor: float = 0.01,
) -> dict[str, float]:
    eligible = [stat for stat in dimension_stats if _eligible_for_selection(stat)]
    raw = {
        stat.value: max(float(stat.current_weight), floor)
        for stat in eligible
    }
    return _normalize(raw)


def exploration_probabilities(
    dimension_stats: list[StrategyStat],
    exploratory_values: list[str],
) -> dict[str, float]:
    by_value = {stat.value: stat for stat in dimension_stats}
    raw: dict[str, float] = {}

    for value in exploratory_values:
        if value in raw:
            continue
        stat = by_value.get(value)
        if stat is not None and not _eligible_for_selection(stat):
            continue
        sample_count = max(0, stat.sample_count) if stat is not None else 0
        raw[value] = 1.0 / (1.0 + sample_count)

    return _normalize(raw)


def select_strategy(
    dimension_stats: list[StrategyStat],
    exploratory_values: list[str],
    rng,
    exploration_rate: float = 0.20,
) -> Selection:
    requested_mode = select_mode(rng, exploration_rate)

    if requested_mode == "explore":
        explore = exploration_probabilities(dimension_stats, exploratory_values)
        if explore:
            value = weighted_choice(list(explore.items()), rng)
            return Selection(value, "explore", "under-sampled exploration")

        exploit = exploit_probabilities(dimension_stats)
        if not exploit:
            raise ValueError("no eligible strategy values")
        value = weighted_choice(list(exploit.items()), rng)
        return Selection(value, "exploit", "exploration fallback to weighted exploit")

    exploit = exploit_probabilities(dimension_stats)
    if not exploit:
        raise ValueError("no eligible strategy values")
    value = weighted_choice(list(exploit.items()), rng)
    return Selection(value, "exploit", "weighted exploit")
