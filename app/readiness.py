import json
import math
from dataclasses import dataclass

from app.planner import CORE_ACTIONS, SAFE_TIME_BUCKETS
from app.strategy_models import AdaptiveConfig

READY = "ready"
DEGRADED = "degraded"
FAILED = "failed"

REQUIRED_COLUMNS = {
    "job_runs": set(),
    "content_posts": {
        "facebook_post_id",
        "category",
        "hook_type",
        "style_type",
        "cta_type",
        "format_type",
        "style_experiment_key",
        "scheduled_for",
        "published_at",
        "strategy_mode",
        "strategy_version",
        "status",
    },
    "content_metrics": {"facebook_post_id", "score_kind", "content_score"},
    "style_registry": {
        "dimension",
        "value",
        "parent_value",
        "status",
        "created_at",
        "promoted_at",
        "retired_at",
    },
    "strategy_stats": {
        "dimension",
        "value",
        "sample_count",
        "weighted_score_14d",
        "recent_score_7d",
        "success_rate",
        "current_weight",
        "last_used_at",
        "status",
        "cooldown_until",
        "retest_after",
        "updated_at",
    },
    "adaptive_config": {
        "id",
        "adaptive_enabled",
        "auto_schedule_enabled",
        "auto_suspend_enabled",
        "exploration_rate",
        "baseline_daily_volume",
        "current_strategy_version",
        "last_good_strategy_version",
    },
    "strategy_versions": {
        "version_id",
        "weights_json",
        "config_json",
        "created_at",
        "reason",
        "is_last_good",
    },
    "daily_plan": {
        "plan_date",
        "slot_id",
        "planned_for",
        "action",
        "category",
        "strategy_mode",
        "strategy_version",
        "status",
        "claim_run_key",
        "claimed_at",
        "finished_at",
        "detail",
        "created_at",
    },
}
CANONICAL_STAT_DIMENSIONS = {
    "category",
    "time_bucket",
    "format_type",
    "hook_type",
    "style_type",
    "cta_type",
}
VALID_STAT_STATUSES = {"insufficient_data", "active", "suspended", "retired"}
VALID_REGISTRY_DIMENSIONS = {"hook", "tone", "cta"}
VALID_REGISTRY_STATUSES = {"baseline", "explore", "active", "retired"}
REGISTRY_TO_STAT = {
    "hook": "hook_type",
    "tone": "style_type",
    "cta": "cta_type",
}


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class ReadinessResult:
    status: str
    checks: tuple[ReadinessCheck, ...]


def aggregate_checks(checks) -> str:
    statuses = {item.status for item in checks}
    if FAILED in statuses:
        return FAILED
    if DEGRADED in statuses:
        return DEGRADED
    return READY


def _finite_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _positive_int_or_none(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("strategy version pointer must be a positive integer or null")
    return value


def _schema_check(execute_fn) -> ReadinessCheck:
    rows = execute_fn("SELECT name FROM sqlite_master WHERE type = 'table'", ())
    existing = {str(row[0]) for row in rows}
    missing_tables = sorted(set(REQUIRED_COLUMNS) - existing)
    if missing_tables:
        return ReadinessCheck(
            "schema",
            FAILED,
            "missing required tables: " + ", ".join(missing_tables),
        )

    missing_columns = []
    for table, required in REQUIRED_COLUMNS.items():
        if not required:
            continue
        columns = {
            str(row[1])
            for row in execute_fn(f"PRAGMA table_info({table})", ())
            if len(row) > 1
        }
        absent = sorted(required - columns)
        if absent:
            missing_columns.append(f"{table}({', '.join(absent)})")

    if missing_columns:
        return ReadinessCheck(
            "schema",
            FAILED,
            "missing required columns: " + "; ".join(missing_columns),
        )
    return ReadinessCheck(
        "schema",
        READY,
        f"{len(REQUIRED_COLUMNS)} required tables and runtime columns present",
    )


def _config_check(execute_fn) -> tuple[ReadinessCheck, AdaptiveConfig | None]:
    rows = execute_fn(
        """
        SELECT id, adaptive_enabled, auto_schedule_enabled, auto_suspend_enabled,
               exploration_rate, baseline_daily_volume,
               current_strategy_version, last_good_strategy_version
        FROM adaptive_config
        ORDER BY id
        """,
        (),
    )
    if len(rows) != 1 or rows[0][0] != 1:
        return (
            ReadinessCheck(
                "adaptive_config",
                FAILED,
                "expected exactly one production config row with id=1",
            ),
            None,
        )

    (
        _,
        adaptive_enabled,
        auto_schedule_enabled,
        auto_suspend_enabled,
        exploration_rate,
        baseline_daily_volume,
        current_strategy_version,
        last_good_strategy_version,
    ) = rows[0]

    booleans = (adaptive_enabled, auto_schedule_enabled, auto_suspend_enabled)
    if any(value not in (0, 1, False, True) for value in booleans):
        return (
            ReadinessCheck(
                "adaptive_config",
                FAILED,
                "kill switches must be stored as 0/1",
            ),
            None,
        )

    exploration = _finite_number(exploration_rate)
    if exploration is None or not 0.0 <= exploration <= 1.0:
        return (
            ReadinessCheck(
                "adaptive_config",
                FAILED,
                "exploration_rate must be finite within [0, 1]",
            ),
            None,
        )
    if (
        isinstance(baseline_daily_volume, bool)
        or not isinstance(baseline_daily_volume, int)
        or baseline_daily_volume <= 0
    ):
        return (
            ReadinessCheck(
                "adaptive_config",
                FAILED,
                "baseline_daily_volume must be a positive integer",
            ),
            None,
        )

    try:
        current = _positive_int_or_none(current_strategy_version)
        last_good = _positive_int_or_none(last_good_strategy_version)
    except ValueError as error:
        return ReadinessCheck("adaptive_config", FAILED, str(error)), None

    config = AdaptiveConfig(
        adaptive_enabled=bool(adaptive_enabled),
        auto_schedule_enabled=bool(auto_schedule_enabled),
        auto_suspend_enabled=bool(auto_suspend_enabled),
        exploration_rate=exploration,
        baseline_daily_volume=baseline_daily_volume,
        current_strategy_version=current,
        last_good_strategy_version=last_good,
    )
    return (
        ReadinessCheck(
            "adaptive_config",
            READY,
            (
                f"id=1 baseline={config.baseline_daily_volume} "
                f"explore={config.exploration_rate:.0%}"
            ),
        ),
        config,
    )


def _strategy_versions_check(execute_fn, config: AdaptiveConfig) -> ReadinessCheck:
    rows = execute_fn(
        """
        SELECT version_id, weights_json, config_json, created_at, reason, is_last_good
        FROM strategy_versions
        ORDER BY version_id
        """,
        (),
    )
    versions = {}
    flagged = []
    for row in rows:
        version_id = row[0]
        if isinstance(version_id, bool) or not isinstance(version_id, int) or version_id <= 0:
            return ReadinessCheck(
                "strategy_versions",
                FAILED,
                f"invalid strategy version id: {version_id!r}",
            )
        try:
            weights = json.loads(str(row[1]))
            snapshot_config = json.loads(str(row[2]))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            return ReadinessCheck(
                "strategy_versions",
                FAILED,
                f"v{version_id} contains malformed JSON: {error}",
            )
        if not isinstance(weights, dict) or not isinstance(snapshot_config, dict):
            return ReadinessCheck(
                "strategy_versions",
                FAILED,
                f"v{version_id} weights/config JSON must be objects",
            )
        versions[version_id] = snapshot_config
        if row[5] not in (0, 1, False, True):
            return ReadinessCheck(
                "strategy_versions",
                FAILED,
                f"v{version_id} is_last_good must be 0/1",
            )
        if bool(row[5]):
            flagged.append(version_id)

    current = config.current_strategy_version
    last_good = config.last_good_strategy_version
    if current is not None and current not in versions:
        return ReadinessCheck(
            "strategy_versions",
            FAILED,
            f"current strategy pointer v{current} has no snapshot",
        )
    if last_good is not None and last_good not in versions:
        return ReadinessCheck(
            "strategy_versions",
            FAILED,
            f"last-good strategy pointer v{last_good} has no snapshot",
        )
    if current is not None and last_good is not None and last_good > current:
        return ReadinessCheck(
            "strategy_versions",
            FAILED,
            f"last-good v{last_good} cannot be newer than current v{current}",
        )

    if last_good is None:
        if flagged:
            return ReadinessCheck(
                "strategy_versions",
                FAILED,
                "last-good pointer is null but audit flags remain set: "
                + ", ".join(f"v{value}" for value in flagged),
            )
    elif flagged != [last_good]:
        return ReadinessCheck(
            "strategy_versions",
            FAILED,
            (
                f"expected exactly last-good v{last_good} to be audit-flagged; "
                f"found {flagged}"
            ),
        )

    if current is not None:
        snapshot_current = versions[current].get("current_strategy_version")
        if snapshot_current != current:
            return ReadinessCheck(
                "strategy_versions",
                FAILED,
                (
                    f"current snapshot v{current} self-pointer is "
                    f"{snapshot_current!r}"
                ),
            )

    if current is None:
        return ReadinessCheck(
            "strategy_versions",
            DEGRADED,
            "adaptive strategy has no learned current version yet",
        )
    if last_good is None:
        return ReadinessCheck(
            "strategy_versions",
            DEGRADED,
            f"current=v{current}; no proven last-good rollback target yet",
        )
    return ReadinessCheck(
        "strategy_versions",
        READY,
        f"current=v{current}, last_good=v{last_good}",
    )


def _strategy_stats_check(execute_fn) -> ReadinessCheck:
    rows = execute_fn(
        """
        SELECT dimension, value, sample_count, weighted_score_14d,
               recent_score_7d, success_rate, current_weight, status
        FROM strategy_stats
        ORDER BY dimension, value
        """,
        (),
    )
    for row in rows:
        dimension = str(row[0])
        value = str(row[1])
        sample_count = row[2]
        status = str(row[7])
        if dimension not in CANONICAL_STAT_DIMENSIONS:
            return ReadinessCheck(
                "strategy_stats",
                FAILED,
                f"unknown strategy dimension: {dimension}",
            )
        if status not in VALID_STAT_STATUSES:
            return ReadinessCheck(
                "strategy_stats",
                FAILED,
                f"{dimension}:{value} has unknown status {status}",
            )
        if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 0:
            return ReadinessCheck(
                "strategy_stats",
                FAILED,
                f"{dimension}:{value} has invalid sample_count {sample_count!r}",
            )

        weighted = _finite_number(row[3])
        recent = _finite_number(row[4])
        success_rate = _finite_number(row[5])
        current_weight = _finite_number(row[6])
        if None in (weighted, recent, success_rate, current_weight):
            return ReadinessCheck(
                "strategy_stats",
                FAILED,
                f"{dimension}:{value} has a non-finite numeric field",
            )
        if not 0.0 <= weighted <= 100.0 or not 0.0 <= recent <= 100.0:
            return ReadinessCheck(
                "strategy_stats",
                FAILED,
                f"{dimension}:{value} score must be within [0, 100]",
            )
        if not 0.0 <= success_rate <= 1.0:
            return ReadinessCheck(
                "strategy_stats",
                FAILED,
                f"{dimension}:{value} success_rate must be within [0, 1]",
            )
        if current_weight < 0.0:
            return ReadinessCheck(
                "strategy_stats",
                FAILED,
                f"{dimension}:{value} has negative current_weight",
            )
        if status in {"suspended", "retired"} and current_weight != 0.0:
            return ReadinessCheck(
                "strategy_stats",
                FAILED,
                f"{dimension}:{value} is {status} but current_weight={current_weight}",
            )
        if dimension == "category" and value not in CORE_ACTIONS:
            return ReadinessCheck(
                "strategy_stats",
                FAILED,
                f"unknown planner category: {value}",
            )
        if (
            dimension == "time_bucket"
            and status not in {"retired", "suspended"}
            and value not in SAFE_TIME_BUCKETS
        ):
            return ReadinessCheck(
                "strategy_stats",
                FAILED,
                f"unsafe active time bucket: {value}",
            )

    return ReadinessCheck(
        "strategy_stats",
        READY,
        f"{len(rows)} strategy rows have valid dimensions, states, and weights",
    )


def _style_registry_check(execute_fn) -> ReadinessCheck:
    rows = execute_fn(
        """
        SELECT id, dimension, value, parent_value, status,
               created_at, promoted_at, retired_at
        FROM style_registry
        ORDER BY id
        """,
        (),
    )
    identities = set()
    registry = {}
    explore_count = 0
    for row in rows:
        _, dimension_raw, value_raw, parent_raw, status_raw, *_ = row
        dimension = str(dimension_raw)
        value = str(value_raw)
        parent = str(parent_raw) if parent_raw not in (None, "") else None
        status = str(status_raw)
        identity = (dimension, value)
        if identity in identities:
            return ReadinessCheck(
                "style_registry",
                FAILED,
                f"duplicate registry identity: {dimension}:{value}",
            )
        identities.add(identity)
        registry[identity] = (status, parent)
        if dimension not in VALID_REGISTRY_DIMENSIONS:
            return ReadinessCheck(
                "style_registry",
                FAILED,
                f"unknown registry dimension: {dimension}",
            )
        if status not in VALID_REGISTRY_STATUSES:
            return ReadinessCheck(
                "style_registry",
                FAILED,
                f"{dimension}:{value} has unknown registry status {status}",
            )
        if status == "baseline" and parent is not None:
            return ReadinessCheck(
                "style_registry",
                FAILED,
                f"baseline {dimension}:{value} cannot have a parent",
            )
        if status == "explore":
            explore_count += 1
            if parent is None:
                return ReadinessCheck(
                    "style_registry",
                    FAILED,
                    f"explore experiment {dimension}:{value} has no parent",
                )

    if explore_count > 1:
        return ReadinessCheck(
            "style_registry",
            FAILED,
            f"multiple pending explore experiments: {explore_count}",
        )

    for (dimension, value), (status, parent) in registry.items():
        if parent is None:
            continue
        parent_row = registry.get((dimension, parent))
        if parent_row is None:
            return ReadinessCheck(
                "style_registry",
                FAILED,
                f"{dimension}:{value} has missing parent {parent}",
            )
        if parent_row[0] == "retired":
            return ReadinessCheck(
                "style_registry",
                FAILED,
                f"{dimension}:{value} has retired parent {parent}",
            )

    stat_rows = execute_fn(
        "SELECT dimension, value, current_weight FROM strategy_stats",
        (),
    )
    weights = {
        (str(dimension), str(value)): _finite_number(weight)
        for dimension, value, weight in stat_rows
    }
    for (registry_dimension, value), (status, _) in registry.items():
        if status not in {"explore", "retired"}:
            continue
        stat_dimension = REGISTRY_TO_STAT[registry_dimension]
        weight = weights.get((stat_dimension, value))
        if weight is not None and weight > 0.0:
            return ReadinessCheck(
                "style_registry",
                FAILED,
                (
                    f"{status} registry value {registry_dimension}:{value} "
                    f"has exploitable weight {weight}"
                ),
            )

    return ReadinessCheck(
        "style_registry",
        READY,
        f"{len(rows)} registry rows valid; pending_explore={explore_count}",
    )


def run_core_checks(execute_fn) -> list[ReadinessCheck]:
    checks = []
    schema = _schema_check(execute_fn)
    checks.append(schema)
    if schema.status == FAILED:
        return checks

    config_check, config = _config_check(execute_fn)
    checks.append(config_check)
    if config is None:
        return checks

    checks.append(_strategy_versions_check(execute_fn, config))
    checks.append(_strategy_stats_check(execute_fn))
    checks.append(_style_registry_check(execute_fn))
    return checks
