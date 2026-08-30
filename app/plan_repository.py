from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


FINAL_STATUSES = {"published", "skipped", "failed", "expired"}


@dataclass(frozen=True)
class DailyPlanSlot:
    plan_date: str
    slot_id: str
    planned_for: str
    action: str
    category: str
    strategy_mode: str
    strategy_version: int | None
    status: str
    claim_run_key: str | None
    claimed_at: str | None
    finished_at: str | None
    detail: str
    created_at: str


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _map_slot(row) -> DailyPlanSlot:
    return DailyPlanSlot(
        plan_date=str(row[0]),
        slot_id=str(row[1]),
        planned_for=str(row[2]),
        action=str(row[3]),
        category=str(row[4]),
        strategy_mode=str(row[5]),
        strategy_version=int(row[6]) if row[6] is not None else None,
        status=str(row[7]),
        claim_run_key=str(row[8]) if row[8] is not None else None,
        claimed_at=str(row[9]) if row[9] is not None else None,
        finished_at=str(row[10]) if row[10] is not None else None,
        detail=str(row[11] or ""),
        created_at=str(row[12]),
    )


def save_slots(execute_fn, slots: list[DailyPlanSlot]) -> None:
    for slot in slots:
        execute_fn(
            """
            INSERT OR IGNORE INTO daily_plan (
                plan_date, slot_id, planned_for, action, category,
                strategy_mode, strategy_version, status, claim_run_key,
                claimed_at, finished_at, detail, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                slot.plan_date,
                slot.slot_id,
                slot.planned_for,
                slot.action,
                slot.category,
                slot.strategy_mode,
                slot.strategy_version,
                slot.status,
                slot.claim_run_key,
                slot.claimed_at,
                slot.finished_at,
                slot.detail,
                slot.created_at,
            ),
        )


def list_slots(execute_fn, plan_date: str) -> list[DailyPlanSlot]:
    rows = execute_fn(
        """
        SELECT plan_date, slot_id, planned_for, action, category,
               strategy_mode, strategy_version, status, claim_run_key,
               claimed_at, finished_at, detail, created_at
        FROM daily_plan
        WHERE plan_date = ?
        ORDER BY planned_for, slot_id
        """,
        (plan_date,),
    )
    return [_map_slot(row) for row in rows]


def claim_due_slot(
    execute_fn,
    *,
    plan_date: str,
    now: datetime,
    run_key: str,
    grace_minutes: int = 20,
) -> DailyPlanSlot | None:
    current = _as_utc(now)
    cutoff = current - timedelta(minutes=max(0, int(grace_minutes)))
    current_iso = current.isoformat()
    cutoff_iso = cutoff.isoformat()

    execute_fn(
        """
        UPDATE daily_plan
        SET status = 'expired', detail = 'expired outside dispatcher grace window'
        WHERE plan_date = ?
          AND status = 'planned'
          AND planned_for < ?
        """,
        (plan_date, cutoff_iso),
    )

    rows = execute_fn(
        """
        UPDATE daily_plan
        SET status = 'claimed', claim_run_key = ?, claimed_at = ?
        WHERE id = (
            SELECT id
            FROM daily_plan
            WHERE plan_date = ?
              AND status = 'planned'
              AND planned_for <= ?
              AND planned_for >= ?
            ORDER BY planned_for, slot_id
            LIMIT 1
        )
          AND status = 'planned'
        RETURNING plan_date, slot_id, planned_for, action, category,
                  strategy_mode, strategy_version, status, claim_run_key,
                  claimed_at, finished_at, detail, created_at
        """,
        (run_key, current_iso, plan_date, current_iso, cutoff_iso),
    )
    if not rows:
        return None
    return _map_slot(rows[0])


def finish_slot(
    execute_fn,
    *,
    plan_date: str,
    slot_id: str,
    run_key: str,
    status: str,
    finished_at: str,
    detail: str = "",
) -> None:
    if status not in FINAL_STATUSES - {"expired"}:
        raise ValueError(f"Trạng thái slot không hợp lệ: {status}")
    execute_fn(
        """
        UPDATE daily_plan
        SET status = ?, finished_at = ?, detail = ?
        WHERE plan_date = ?
          AND slot_id = ?
          AND claim_run_key = ?
          AND status = 'claimed'
        """,
        (status, finished_at, detail[:1000], plan_date, slot_id, run_key),
    )
