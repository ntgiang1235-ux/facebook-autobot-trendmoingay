from dataclasses import asdict, dataclass


DEFAULT_BASELINE_DAILY_VOLUME = 12


@dataclass(frozen=True)
class AdaptiveConfig:
    adaptive_enabled: bool = True
    auto_schedule_enabled: bool = True
    auto_suspend_enabled: bool = True
    exploration_rate: float = 0.20
    baseline_daily_volume: int = DEFAULT_BASELINE_DAILY_VOLUME
    current_strategy_version: int | None = None
    last_good_strategy_version: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class StrategyStat:
    dimension: str
    value: str
    sample_count: int
    weighted_score_14d: float
    recent_score_7d: float
    success_rate: float
    current_weight: float
    last_used_at: str | None
    status: str
    cooldown_until: str | None
    retest_after: str | None
    updated_at: str


@dataclass(frozen=True)
class StrategySnapshot:
    version_id: int
    weights: dict[str, dict[str, float]]
    config: AdaptiveConfig
    created_at: str
    reason: str
    is_last_good: bool = False
