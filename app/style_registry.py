from dataclasses import dataclass
from datetime import datetime, timezone


VALID_STATUSES = {"baseline", "explore", "active", "retired"}
SEED_STYLES = {
    "hook": (
        "question",
        "number",
        "surprising_fact",
        "direct_statement",
        "contrast",
        "curiosity",
    ),
    "tone": (
        "concise_news",
        "conversational",
        "witty",
        "explanatory",
        "reflective",
    ),
    "cta": (
        "opinion_question",
        "choose_side",
        "experience_share",
        "save_for_later",
        "no_cta",
    ),
}


@dataclass(frozen=True)
class StyleVariant:
    id: int
    dimension: str
    value: str
    parent_value: str | None
    status: str
    created_at: str
    promoted_at: str | None
    retired_at: str | None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_seed_styles(execute_fn) -> None:
    created_at = _utc_now()
    for dimension, values in SEED_STYLES.items():
        for value in values:
            execute_fn(
                """
                INSERT OR IGNORE INTO style_registry (
                    dimension, value, parent_value, status, created_at,
                    promoted_at, retired_at
                )
                VALUES (?, ?, ?, ?, ?, NULL, NULL)
                """,
                (dimension, value, None, "baseline", created_at),
            )


def list_active_styles(execute_fn, dimension: str) -> list[StyleVariant]:
    rows = execute_fn(
        """
        SELECT id, dimension, value, parent_value, status, created_at,
               promoted_at, retired_at
        FROM style_registry
        WHERE dimension = ? AND status != 'retired'
        ORDER BY id
        """,
        (dimension,),
    )
    return [StyleVariant(*row) for row in rows]


def register_experiment(
    execute_fn,
    dimension: str,
    value: str,
    parent_value: str | None,
    *,
    created_at: str | None = None,
) -> int | None:
    timestamp = created_at or _utc_now()
    rows = execute_fn(
        """
        INSERT OR IGNORE INTO style_registry (
            dimension, value, parent_value, status, created_at,
            promoted_at, retired_at
        )
        VALUES (?, ?, ?, ?, ?, NULL, NULL)
        RETURNING id
        """,
        (dimension, value, parent_value, "explore", timestamp),
    )
    if rows:
        return int(rows[0][0])

    existing = execute_fn(
        "SELECT id FROM style_registry WHERE dimension = ? AND value = ?",
        (dimension, value),
    )
    return int(existing[0][0]) if existing else None


def set_style_status(
    execute_fn,
    style_id: int,
    status: str,
    *,
    changed_at: str | None = None,
) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"unknown style status: {status}")

    timestamp = changed_at or _utc_now()
    if status == "active":
        execute_fn(
            "UPDATE style_registry SET status = ?, promoted_at = ? WHERE id = ?",
            (status, timestamp, style_id),
        )
    elif status == "retired":
        execute_fn(
            "UPDATE style_registry SET status = ?, retired_at = ? WHERE id = ?",
            (status, timestamp, style_id),
        )
    else:
        execute_fn(
            "UPDATE style_registry SET status = ? WHERE id = ?",
            (status, style_id),
        )
