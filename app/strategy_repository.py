import json

from app.strategy_models import AdaptiveConfig, StrategySnapshot, StrategyStat


CONFIG_ROW_ID = 1


def load_config(execute_fn) -> AdaptiveConfig:
    rows = execute_fn(
        """
        SELECT adaptive_enabled, auto_schedule_enabled, auto_suspend_enabled,
               exploration_rate, baseline_daily_volume, current_strategy_version,
               last_good_strategy_version
        FROM adaptive_config
        WHERE id = ?
        """,
        (CONFIG_ROW_ID,),
    )
    if not rows:
        return AdaptiveConfig()

    row = rows[0]
    return AdaptiveConfig(
        adaptive_enabled=bool(row[0]),
        auto_schedule_enabled=bool(row[1]),
        auto_suspend_enabled=bool(row[2]),
        exploration_rate=float(row[3]),
        baseline_daily_volume=int(row[4]),
        current_strategy_version=int(row[5]) if row[5] is not None else None,
        last_good_strategy_version=int(row[6]) if row[6] is not None else None,
    )


def save_config(execute_fn, config: AdaptiveConfig) -> None:
    execute_fn(
        """
        INSERT INTO adaptive_config (
            id, adaptive_enabled, auto_schedule_enabled, auto_suspend_enabled,
            exploration_rate, baseline_daily_volume, current_strategy_version,
            last_good_strategy_version
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            adaptive_enabled = excluded.adaptive_enabled,
            auto_schedule_enabled = excluded.auto_schedule_enabled,
            auto_suspend_enabled = excluded.auto_suspend_enabled,
            exploration_rate = excluded.exploration_rate,
            baseline_daily_volume = excluded.baseline_daily_volume,
            current_strategy_version = excluded.current_strategy_version,
            last_good_strategy_version = excluded.last_good_strategy_version
        """,
        (
            CONFIG_ROW_ID,
            int(config.adaptive_enabled),
            int(config.auto_schedule_enabled),
            int(config.auto_suspend_enabled),
            float(config.exploration_rate),
            int(config.baseline_daily_volume),
            config.current_strategy_version,
            config.last_good_strategy_version,
        ),
    )


def _map_stat(row) -> StrategyStat:
    return StrategyStat(
        dimension=str(row[0]),
        value=str(row[1]),
        sample_count=int(row[2]),
        weighted_score_14d=float(row[3]),
        recent_score_7d=float(row[4]),
        success_rate=float(row[5]),
        current_weight=float(row[6]),
        last_used_at=str(row[7]) if row[7] is not None else None,
        status=str(row[8]),
        cooldown_until=str(row[9]) if row[9] is not None else None,
        retest_after=str(row[10]) if row[10] is not None else None,
        updated_at=str(row[11]),
    )


def load_stats(execute_fn, dimension: str | None = None) -> list[StrategyStat]:
    base_query = """
        SELECT dimension, value, sample_count, weighted_score_14d, recent_score_7d,
               success_rate, current_weight, last_used_at, status, cooldown_until,
               retest_after, updated_at
        FROM strategy_stats
    """
    if dimension is None:
        rows = execute_fn(base_query + " ORDER BY dimension, value", ())
    else:
        rows = execute_fn(base_query + " WHERE dimension = ? ORDER BY value", (dimension,))
    return [_map_stat(row) for row in rows]


def upsert_stat(execute_fn, stat: StrategyStat) -> None:
    execute_fn(
        """
        INSERT INTO strategy_stats (
            dimension, value, sample_count, weighted_score_14d, recent_score_7d,
            success_rate, current_weight, last_used_at, status, cooldown_until,
            retest_after, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(dimension, value) DO UPDATE SET
            sample_count = excluded.sample_count,
            weighted_score_14d = excluded.weighted_score_14d,
            recent_score_7d = excluded.recent_score_7d,
            success_rate = excluded.success_rate,
            current_weight = excluded.current_weight,
            last_used_at = excluded.last_used_at,
            status = excluded.status,
            cooldown_until = excluded.cooldown_until,
            retest_after = excluded.retest_after,
            updated_at = excluded.updated_at
        """,
        (
            stat.dimension,
            stat.value,
            stat.sample_count,
            stat.weighted_score_14d,
            stat.recent_score_7d,
            stat.success_rate,
            stat.current_weight,
            stat.last_used_at,
            stat.status,
            stat.cooldown_until,
            stat.retest_after,
            stat.updated_at,
        ),
    )


def save_strategy_version(execute_fn, snapshot: StrategySnapshot) -> None:
    weights_json = json.dumps(snapshot.weights, ensure_ascii=False, sort_keys=True)
    config_json = json.dumps(snapshot.config.to_dict(), ensure_ascii=False, sort_keys=True)
    execute_fn(
        """
        INSERT INTO strategy_versions (
            version_id, weights_json, config_json, created_at, reason, is_last_good
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot.version_id,
            weights_json,
            config_json,
            snapshot.created_at,
            snapshot.reason,
            int(snapshot.is_last_good),
        ),
    )


def load_strategy_version(execute_fn, version_id: int) -> StrategySnapshot | None:
    rows = execute_fn(
        """
        SELECT version_id, weights_json, config_json, created_at, reason, is_last_good
        FROM strategy_versions
        WHERE version_id = ?
        """,
        (version_id,),
    )
    if not rows:
        return None

    row = rows[0]
    weights = json.loads(row[1])
    config_data = json.loads(row[2])
    config = AdaptiveConfig(**config_data)
    return StrategySnapshot(
        version_id=int(row[0]),
        weights=weights,
        config=config,
        created_at=str(row[3]),
        reason=str(row[4]),
        is_last_good=bool(row[5]),
    )
