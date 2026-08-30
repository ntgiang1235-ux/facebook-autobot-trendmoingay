import hashlib
import math
import random
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from app.plan_repository import DailyPlanSlot
from app.selection import select_strategy
from app.strategy_models import AdaptiveConfig, StrategyStat


VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
CORE_ACTIONS = ("post", "finance", "philosophy", "fun", "recipe", "video")
MIN_MATURE_SAMPLES = 5
MIN_READY_CATEGORIES = 3

# Stable baseline preserves the existing publishing rhythm while moving schedule
# ownership from workflow YAML into Turso.
BASELINE_SLOTS = (
    ("08:30", "post"),
    ("09:00", "philosophy"),
    ("09:30", "video"),
    ("11:00", "finance"),
    ("11:30", "post"),
    ("12:30", "video"),
    ("13:30", "fun"),
    ("14:30", "post"),
    ("16:00", "recipe"),
    ("17:30", "video"),
    ("18:30", "post"),
    ("20:00", "fun"),
)
EXTRA_SLOTS = (("10:30", "post"), ("21:00", "video"))
REDUCTION_PRIORITY = ("12:30", "11:30")


def _as_utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _stable_rng(plan_date: date, version: int | None) -> random.Random:
    raw = f"{plan_date.isoformat()}:{version if version is not None else 'baseline'}"
    seed = int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16], 16)
    return random.Random(seed)


def _parse_optional_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _local_slot_to_utc(plan_date: date, hhmm: str) -> str:
    hour, minute = (int(part) for part in hhmm.split(":", 1))
    local = datetime.combine(plan_date, time(hour=hour, minute=minute), tzinfo=VIETNAM_TZ)
    return local.astimezone(timezone.utc).isoformat()


def target_daily_volume(baseline_daily_volume: int, overall_score: float) -> int:
    """Smoothly adjust daily volume while enforcing the approved ±20% guardrail."""
    baseline = max(1, int(baseline_daily_volume))
    score = max(0.0, min(100.0, float(overall_score)))
    adjustment = max(-0.20, min(0.20, (score - 50.0) / 250.0))
    lower = math.ceil(baseline * 0.80)
    upper = math.floor(baseline * 1.20)
    upper = max(lower, upper)
    target = round(baseline * (1.0 + adjustment))
    return max(lower, min(upper, target))


def _baseline_template(target: int) -> list[tuple[str, str]]:
    slots = list(BASELINE_SLOTS)
    if target > len(slots):
        slots.extend(EXTRA_SLOTS[: target - len(slots)])
    elif target < len(slots):
        for hhmm in REDUCTION_PRIORITY:
            if len(slots) <= target:
                break
            slots = [entry for entry in slots if entry[0] != hhmm]
        while len(slots) > target:
            # Remove a duplicate-rich late slot before ever removing the only
            # representative of a core category.
            counts = {action: sum(1 for _, a in slots if a == action) for action in CORE_ACTIONS}
            removable_index = next(
                (
                    index
                    for index in range(len(slots) - 1, -1, -1)
                    if counts.get(slots[index][1], 0) > 1
                ),
                len(slots) - 1,
            )
            slots.pop(removable_index)
    return sorted(slots, key=lambda entry: entry[0])


def _learning_ready(category_stats: list[StrategyStat]) -> bool:
    mature = [
        stat
        for stat in category_stats
        if stat.dimension == "category"
        and stat.sample_count >= MIN_MATURE_SAMPLES
        and stat.status not in {"retired"}
    ]
    return len(mature) >= MIN_READY_CATEGORIES


def _due_retests(category_stats: list[StrategyStat], now: datetime) -> list[StrategyStat]:
    due = []
    for stat in category_stats:
        if stat.dimension != "category" or stat.status != "suspended":
            continue
        retest_at = _parse_optional_iso(stat.retest_after)
        if retest_at is not None and retest_at <= now:
            due.append(stat)
    return sorted(due, key=lambda stat: stat.value)


def _active_stats(category_stats: list[StrategyStat], now: datetime) -> list[StrategyStat]:
    active = []
    for stat in category_stats:
        if stat.dimension != "category" or stat.value not in CORE_ACTIONS:
            continue
        if stat.status in {"retired"}:
            continue
        if stat.status == "suspended":
            # Suspended categories are only admitted through the explicit
            # controlled re-test reservation below.
            continue
        active.append(stat)
    return active


def _overall_score(active_stats: list[StrategyStat]) -> float:
    if not active_stats:
        return 50.0
    weights = [max(0.01, float(stat.current_weight)) for stat in active_stats]
    total = sum(weights)
    return sum(stat.weighted_score_14d * weight for stat, weight in zip(active_stats, weights)) / total


def _adaptive_actions(
    target: int,
    active_stats: list[StrategyStat],
    due_retests: list[StrategyStat],
    config: AdaptiveConfig,
    rng: random.Random,
) -> list[tuple[str, str]]:
    reserved: list[tuple[str, str]] = []
    for stat in due_retests:
        if len(reserved) >= target:
            break
        reserved.append((stat.value, "retest"))

    remaining = target - len(reserved)
    active_values = [stat.value for stat in active_stats]
    chosen: list[tuple[str, str]] = []

    # Preserve variety: one slot for each active category before weighted fill.
    for value in active_values:
        if len(chosen) >= remaining:
            break
        chosen.append((value, "exploit"))

    while len(chosen) < remaining and active_stats:
        selection = select_strategy(
            active_stats,
            active_values,
            rng,
            exploration_rate=config.exploration_rate,
        )
        chosen.append((selection.value, selection.mode))

    if len(chosen) < remaining:
        fallback = [action for _, action in BASELINE_SLOTS]
        index = 0
        while len(chosen) < remaining:
            chosen.append((fallback[index % len(fallback)], "baseline"))
            index += 1

    return chosen + reserved


def build_daily_plan(
    plan_date: date,
    config: AdaptiveConfig,
    category_stats: list[StrategyStat],
    time_stats: list[StrategyStat],
    *,
    now: datetime | None = None,
) -> list[DailyPlanSlot]:
    """Build one deterministic plan; workflow timing stays generic and immutable."""
    del time_stats  # Time-bucket learning can be layered in without changing the plan contract.
    created = _as_utc(now)
    adaptive_allowed = (
        config.adaptive_enabled
        and config.auto_schedule_enabled
        and _learning_ready(category_stats)
    )

    if not adaptive_allowed:
        target = max(1, int(config.baseline_daily_volume))
        template = _baseline_template(target)
        action_modes = [(action, "baseline") for _, action in template]
        local_times = [hhmm for hhmm, _ in template]
    else:
        active = _active_stats(category_stats, created)
        due = _due_retests(category_stats, created)
        target = target_daily_volume(config.baseline_daily_volume, _overall_score(active))
        action_modes = _adaptive_actions(target, active, due, config, _stable_rng(plan_date, config.current_strategy_version))
        template = _baseline_template(target)
        local_times = [hhmm for hhmm, _ in template]

    # Pair strategy choices with stable half-hour opportunities, then sort by time.
    rows = sorted(zip(local_times, action_modes), key=lambda item: item[0])
    counters: dict[str, int] = {}
    slots: list[DailyPlanSlot] = []
    for hhmm, (action, mode) in rows:
        counters[action] = counters.get(action, 0) + 1
        slot_id = f"{hhmm.replace(':', '')}-{action}-{counters[action]:02d}"
        slots.append(
            DailyPlanSlot(
                plan_date=plan_date.isoformat(),
                slot_id=slot_id,
                planned_for=_local_slot_to_utc(plan_date, hhmm),
                action=action,
                category=action,
                strategy_mode=mode,
                strategy_version=config.current_strategy_version,
                status="planned",
                claim_run_key=None,
                claimed_at=None,
                finished_at=None,
                detail="",
                created_at=created.isoformat(),
            )
        )
    return slots
