from dataclasses import replace

from app.readiness import DEGRADED, READY, ReadinessResult, aggregate_checks


LEARNING_PREFIX = "insufficient mature learning for: "
LAST_GOOD_MARKER = "no proven last-good rollback target yet"


def apply_bootstrap_policy(result: ReadinessResult) -> ReadinessResult:
    """Treat guarded baseline bootstrap as ready without hiding maturity state.

    The planner already falls back to deterministic baseline behavior while
    category/time learning is immature. During that bootstrap window a missing
    proven last-good strategy is not yet an operational rollback gap because
    adaptive scheduling is not mature enough to rely on learned routing.
    """
    learning = next((item for item in result.checks if item.name == "learning"), None)
    bootstrap_missing = (
        learning is not None
        and learning.status == DEGRADED
        and learning.detail.startswith(LEARNING_PREFIX)
    )
    if not bootstrap_missing:
        return result

    pending = learning.detail[len(LEARNING_PREFIX) :]
    adjusted = []
    for item in result.checks:
        if item.name == "learning":
            adjusted.append(
                replace(
                    item,
                    status=READY,
                    detail=f"bootstrap-safe; adaptive maturity pending for: {pending}",
                )
            )
            continue
        if (
            item.name == "strategy_versions"
            and item.status == DEGRADED
            and LAST_GOOD_MARKER in item.detail
        ):
            adjusted.append(
                replace(
                    item,
                    status=READY,
                    detail=item.detail.replace(
                        LAST_GOOD_MARKER,
                        f"bootstrap-safe; {LAST_GOOD_MARKER}",
                    ),
                )
            )
            continue
        adjusted.append(item)

    checks = tuple(adjusted)
    return ReadinessResult(aggregate_checks(checks), checks)
