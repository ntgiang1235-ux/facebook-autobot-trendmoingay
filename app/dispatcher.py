from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.job_contract import JobOutcome, skipped, success
from app.plan_repository import claim_due_slot, finish_slot


VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _as_utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def local_plan_date(now: datetime | None = None) -> str:
    return _as_utc(now).astimezone(VIETNAM_TZ).date().isoformat()


def _finish_failure_best_effort(execute_fn, slot, run_key: str, now: datetime, detail: str) -> None:
    try:
        finish_slot(
            execute_fn,
            plan_date=slot.plan_date,
            slot_id=slot.slot_id,
            run_key=run_key,
            status="failed",
            finished_at=now.isoformat(),
            detail=detail,
        )
    except Exception as finish_exc:
        # Never replace the original business/dispatch failure with a secondary
        # persistence error. The outer hardening contract will still fail CI.
        print(f"⚠️ Không ghi được trạng thái slot failed: {finish_exc}")


def dispatch_due(
    execute_fn,
    jobs: dict[str, object],
    *,
    now: datetime | None = None,
    run_key: str,
    grace_minutes: int = 20,
) -> JobOutcome:
    current = _as_utc(now)
    plan_date = local_plan_date(current)
    slot = claim_due_slot(
        execute_fn,
        plan_date=plan_date,
        now=current,
        run_key=run_key,
        grace_minutes=grace_minutes,
    )
    if slot is None:
        return skipped("no due plan slot")

    job_fn = jobs.get(slot.action)
    if job_fn is None:
        error = ValueError(f"Unknown planned action: {slot.action}")
        _finish_failure_best_effort(execute_fn, slot, run_key, current, str(error))
        raise error

    try:
        result = job_fn()
    except Exception as exc:
        _finish_failure_best_effort(execute_fn, slot, run_key, current, str(exc))
        raise

    outcome = result if isinstance(result, JobOutcome) else success()
    if outcome.status == "failed":
        error = RuntimeError(outcome.detail or f"Planned action failed: {slot.action}")
        _finish_failure_best_effort(execute_fn, slot, run_key, current, str(error))
        raise error

    plan_status = "skipped" if outcome.status == "skipped" else "published"
    finish_slot(
        execute_fn,
        plan_date=slot.plan_date,
        slot_id=slot.slot_id,
        run_key=run_key,
        status=plan_status,
        finished_at=current.isoformat(),
        detail=outcome.detail,
    )
    return outcome
