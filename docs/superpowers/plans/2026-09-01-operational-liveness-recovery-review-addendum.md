# Phase 4N.2 Operational Liveness Review Addendum

Post-implementation review tightened four operational boundaries without changing the approved content, quality, learning, or scheduling strategy.

## 1. Pre-window dispatcher guard

- Before 08:30 in `Asia/Ho_Chi_Minh`, `dispatch_due()` returns `skipped`.
- It does not self-heal `daily_plan` and does not attempt an atomic claim before that boundary.
- This prevents an earlier scheduled wake such as health or learning from materializing the day plan before the intended `learn -> strategy_guard -> planner` preparation path.
- At or after 08:30, a missing current-day plan is still self-healed idempotently before claim.

## 2. Opportunistic dispatch failure isolation

- A non-dispatch GitHub `schedule` wake may attempt one secondary opportunistic dispatch with a distinct dispatch run key.
- That secondary dispatch retains truthful observability: it records `failed` and sends its own failure alert when publishing fails.
- A secondary dispatch failure does not suppress the primary scheduled action such as `health`, `metrics`, `learn`, `strategy_guard`, `style_evolve`, or `planner`.
- Primary action failures remain fail-fast.
- Manual runs gain no opportunistic publishing side effect, and explicit `dispatch` runs are not doubled.

## 3. Readiness schema contract

The read-only readiness verifier now treats the following `job_runs` columns as runtime requirements because the liveness check queries them directly:

- `action`
- `status`
- `started_at`

A missing required liveness column is reported by the structured `schema` readiness check rather than surfacing later as an unstructured query error.

## 4. Scheduled publication evidence

Operational liveness counts only confirmed scheduled adaptive publishes as evidence that the publishing loop is alive:

- `status = 'published'`
- `facebook_post_id IS NOT NULL`
- `strategy_mode IN ('baseline', 'exploit', 'explore', 'retest')`
- `scheduled_for IS NOT NULL`
- `published_at` is inside the current Vietnam calendar day

A manual rescue publish therefore cannot hide a scheduler outage after multiple planned slots have elapsed.

## TDD evidence

- Pre-window guard: RED CI #346 -> GREEN CI #347, 377 tests passing.
- Readiness `job_runs` schema contract: RED CI #348 -> GREEN CI #349, 378 tests passing.
- Opportunistic dispatch failure isolation: RED CI #350 -> GREEN CI #351, 379 tests passing.
- Manual-publish false-READY guard: RED CI #352 -> GREEN CI #353, 380 tests passing plus `compileall` passing.

These refinements preserve the core Phase 4N.2 invariants: one atomic claim per dispatch opportunity, no backlog flood, no pre-08:30 plan creation by dispatcher, no manual publishing side effects, and read-only readiness verification.