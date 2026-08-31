from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app import plan_repository, planner, strategy_repository
from app.job_contract import JobOutcome, skipped, success


VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _as_utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def create_daily_plan(execute_fn, now: datetime | None = None) -> JobOutcome:
    current = _as_utc(now)
    plan_date = current.astimezone(VIETNAM_TZ).date()
    config = strategy_repository.load_config(execute_fn)
    category_stats = strategy_repository.load_stats(execute_fn, "category")
    time_stats = strategy_repository.load_stats(execute_fn, "time_bucket")
    slots = planner.build_daily_plan(
        plan_date,
        config,
        category_stats,
        time_stats,
        now=current,
    )
    plan_repository.save_slots(execute_fn, slots)
    return success(f"planned {len(slots)} slots for {plan_date.isoformat()}")


def ensure_daily_plan(execute_fn, now: datetime | None = None) -> JobOutcome:
    """Create today's Vietnam plan only when no persisted slot exists yet."""
    current = _as_utc(now)
    plan_date = current.astimezone(VIETNAM_TZ).date().isoformat()
    existing = execute_fn(
        "SELECT 1 FROM daily_plan WHERE plan_date = ? LIMIT 1",
        (plan_date,),
    )
    if existing:
        return skipped(f"plan already exists for {plan_date}")
    return create_daily_plan(execute_fn, now=current)
