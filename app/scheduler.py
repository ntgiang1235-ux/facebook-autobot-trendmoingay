from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class ScheduleMetadata:
    scheduled_for: datetime | None
    delay_minutes: int | None
    stale: bool


def schedule_metadata(
    cron: str,
    *,
    now: datetime | None = None,
    stale_after_minutes: int = 60,
) -> ScheduleMetadata:
    """Return timing metadata for the most recent daily GitHub cron occurrence."""
    cron = (cron or "").strip()
    if not cron:
        return ScheduleMetadata(None, None, False)

    fields = cron.split()
    if len(fields) != 5 or fields[2:] != ["*", "*", "*"]:
        raise ValueError(f"Chỉ hỗ trợ cron hằng ngày dạng 'M H * * *': {cron}")

    minute = int(fields[0])
    hour = int(fields[1])
    if not 0 <= minute <= 59 or not 0 <= hour <= 23:
        raise ValueError(f"Cron không hợp lệ: {cron}")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)

    scheduled_for = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if scheduled_for > current:
        scheduled_for -= timedelta(days=1)

    delay_minutes = max(0, int((current - scheduled_for).total_seconds() // 60))
    return ScheduleMetadata(
        scheduled_for=scheduled_for,
        delay_minutes=delay_minutes,
        stale=delay_minutes > stale_after_minutes,
    )
