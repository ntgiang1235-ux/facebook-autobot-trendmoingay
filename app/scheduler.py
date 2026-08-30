from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class ScheduleMetadata:
    scheduled_for: datetime | None
    delay_minutes: int | None
    stale: bool


def _cron_not_supported(cron: str) -> ValueError:
    return ValueError(f"Cron không hỗ trợ: {cron}")


def _parse_minute_list(value: str, cron: str) -> list[int]:
    try:
        minutes = sorted({int(part) for part in value.split(",")})
    except ValueError as exc:
        raise _cron_not_supported(cron) from exc
    if not minutes or any(minute < 0 or minute > 59 for minute in minutes):
        raise _cron_not_supported(cron)
    return minutes


def schedule_metadata(
    cron: str,
    *,
    now: datetime | None = None,
    stale_after_minutes: int = 60,
) -> ScheduleMetadata:
    """Return timing metadata for supported daily or recurring GitHub cron schedules."""
    cron = (cron or "").strip()
    if not cron:
        return ScheduleMetadata(None, None, False)

    fields = cron.split()
    if len(fields) != 5 or fields[2:] != ["*", "*", "*"]:
        raise _cron_not_supported(cron)

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)

    minute_field, hour_field = fields[:2]

    if hour_field == "*":
        # The dispatcher uses a small explicit minute list (for example
        # ``7,37 * * * *``) so GitHub only wakes the bot twice per hour while
        # Turso remains the source of truth for actual publishing slots.
        minutes = _parse_minute_list(minute_field, cron)
        eligible = [minute for minute in minutes if minute <= current.minute]
        if eligible:
            scheduled_for = current.replace(
                minute=max(eligible), second=0, microsecond=0
            )
        else:
            scheduled_for = (
                current.replace(minute=max(minutes), second=0, microsecond=0)
                - timedelta(hours=1)
            )
    else:
        # Preserve the original fixed daily cron contract used by health,
        # reply, summary, metrics and planner jobs.
        if "," in minute_field:
            raise _cron_not_supported(cron)
        try:
            minute = int(minute_field)
            hour = int(hour_field)
        except ValueError as exc:
            raise _cron_not_supported(cron) from exc
        if not 0 <= minute <= 59 or not 0 <= hour <= 23:
            raise _cron_not_supported(cron)

        scheduled_for = current.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if scheduled_for > current:
            scheduled_for -= timedelta(days=1)

    delay_minutes = max(0, int((current - scheduled_for).total_seconds() // 60))
    return ScheduleMetadata(
        scheduled_for=scheduled_for,
        delay_minutes=delay_minutes,
        stale=delay_minutes > stale_after_minutes,
    )
