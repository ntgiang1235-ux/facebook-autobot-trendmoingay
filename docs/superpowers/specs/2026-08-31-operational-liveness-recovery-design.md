# Phase 4N.2 Operational Liveness Recovery Design

## Problem

On 2026-08-31 production had no Facebook posts. Read-only diagnosis showed:

- `daily_plan` had zero rows for 2026-08-31.
- `content_posts` had zero rows for the Vietnam calendar day.
- dispatcher jobs that did run returned `no due plan slot`.
- the scheduled planner was created by GitHub about 329 minutes late and the pre-PR-23 code stale-skipped it.
- GitHub scheduled runs themselves were created late, so this was not primarily an in-workflow concurrency queue.

The architecture currently depends on `planner -> persisted daily_plan -> dispatcher`. A missing planner therefore becomes a whole-day publishing outage. Readiness previously built a safe shadow plan but did not prove that today's plan existed or that the production publishing loop was alive.

## Goal

Prevent a single missed/delayed planner wake from causing a zero-post day, increase the number of opportunities to service due content slots, and make readiness fail when production scheduling/publishing is operationally dead.

This is a P0 liveness recovery. It does not promise exact 12-post delivery when GitHub Actions itself creates only a few scheduled runs in a day; that infrastructure limitation remains observable rather than hidden.

## Chosen approach

Use the existing adaptive planner and atomic dispatcher as the only scheduling authority. Add self-healing around them rather than reintroducing fixed publish crons or creating a second scheduler.

Rejected alternatives:

1. Restore fixed per-category publish crons. Rejected because two scheduling systems could compete and duplicate/contradict adaptive decisions.
2. Add an external cron provider immediately. Rejected for this P0 because it adds new infrastructure and secrets before the application-level single-point-of-failure is removed. External wake reliability can be evaluated separately if GitHub continues to under-deliver runs.

## P0.1 Idempotent today-plan fallback

Add an idempotent helper in `app/adaptive_jobs.py` that guarantees the current Vietnam calendar day's plan exists.

Behavior:

- Determine the Vietnam local date from `now`.
- Query `daily_plan` for that date.
- If at least one row exists, return a no-op outcome and never rebuild or rewrite the plan.
- If no rows exist, call the existing `create_daily_plan()` path, which reuses `planner.build_daily_plan()` and `INSERT OR IGNORE` persistence.
- No separate baseline planner implementation is allowed.
- Concurrent callers remain safe because `daily_plan` already has `UNIQUE(plan_date, slot_id)` and persistence uses `INSERT OR IGNORE`.

The dispatcher must call this guarantee before trying to claim a due slot. If a plan is created late in the day, `claim_due_slot()` will expire old slots outside its grace window and may claim only a currently due slot. It must not publish a backlog of old content.

## P0.2 Opportunistic dispatch on scheduled wake-ups

Every GitHub `schedule` wake-up should become an opportunity to service one currently due adaptive content slot, even when the wake-up's primary action is health, metrics, reply, report, learning, strategy guard, style evolution, or planner.

Rules:

- Manual `workflow_dispatch` actions do not gain implicit publishing side effects.
- The explicit `dispatch` action keeps its existing behavior and is not double-dispatched.
- For other scheduled actions, run one opportunistic dispatch attempt using the same atomic `daily_plan` claim path.
- The opportunistic dispatch gets its own run key and `job_runs` record with action `dispatch`, so observability remains truthful.
- It runs before the primary action's stale early-return. This is safe because dispatcher grace still limits publishing to the current due window; a five-hour-late health run cannot publish a five-hour-old slot.
- At most one slot is attempted per scheduled wake-up.
- Atomic claim remains the duplicate-publish guard when scheduled runs overlap.
- A real dispatch exception is surfaced as a failed dispatch record/notification; it is not silently converted to success.

This improves liveness but cannot manufacture wake-ups GitHub never creates. If GitHub continues creating only a few runs per day, readiness must expose that condition.

## P0.3 Operational liveness readiness gate

Extend `app/readiness.py` with a production-state liveness check. The existing readiness database adapter remains strictly read-only.

Inputs:

- raw `adaptive_config`
- today's `daily_plan`
- recent `job_runs` for action `dispatch`
- today's `content_posts`
- fixed/injected `now` for deterministic tests

Semantics when adaptive scheduling is enabled:

1. Before the first safe slot (08:30 Vietnam), missing today's persisted plan is `DEGRADED`, not `FAILED`, because the publishing day has not begun.
2. At or after 08:30, missing today's `daily_plan` is `FAILED`.
3. Once the publishing day has begun, if there has been no dispatcher run within the last 90 minutes, liveness is `FAILED`. This intentionally detects GitHub wake starvation.
4. When at least three planned slots are more than the dispatcher grace window in the past, zero successfully published content rows for the Vietnam day is `FAILED`.
5. A plan that exists and a recent dispatcher with some successful publication activity is `READY`.
6. Legitimate learning cold-start (`strategy_versions`, mature category/time evidence) remains separate `DEGRADED` state and cannot mask a liveness `FAILED`.

The check must report concrete counts/timestamps so an operator can distinguish missing plan, missing wake-ups, and zero publication.

## Runner and workflow behavior

`readiness_runner.py` continues to aggregate checks with current semantics: any `FAILED` => exit 1; otherwise `DEGRADED` => exit 0; otherwise `READY`.

No new production secrets are required. No Facebook/Gemini/Pexels calls are added to readiness.

The existing Facebook workflow schedule remains unchanged in this P0. The application should tolerate a missed planner and use all scheduled wakes more effectively before changing scheduler infrastructure.

## Safety invariants

- No duplicate scheduling authority.
- No catch-up flood: one currently due slot maximum per wake-up.
- No publishing of slots older than existing dispatcher grace.
- Manual maintenance actions do not unexpectedly publish.
- `daily_plan` atomic claim remains the source of truth for ownership.
- Readiness stays read-only.
- Existing dedup, quality gate, publication ledger, strategy metadata, and Facebook publish adapters remain unchanged.

## TDD plan

### RED group 1 — plan self-healing

Tests prove:

- dispatcher creates today's plan when none exists and can claim a currently due slot.
- an existing plan is not rebuilt or mutated.
- a late-created plan does not publish slots outside the grace window.
- concurrent/idempotent persistence still produces one logical slot set.

### RED group 2 — opportunistic scheduled dispatch

Tests prove:

- a scheduled non-dispatch action attempts one dispatch.
- manual invocation does not opportunistically publish.
- explicit dispatch is not executed twice.
- stale scheduled primary actions can still service a current due slot before their own stale skip.
- publish actions keep their normal stale protection.
- opportunistic dispatch has a distinct `job_runs` identity.

### RED group 3 — readiness liveness

Tests prove:

- no plan before 08:30 => degraded.
- no plan after 08:30 => failed.
- stale/missing dispatcher (>90 minutes) => failed.
- three elapsed slots with zero published content => failed.
- healthy plan + recent dispatch + publication => ready.
- cold-start learning degradation cannot override a liveness failure.

## Verification and rollout

1. Full unit suite and compileall on the feature branch.
2. Review diff for accidental changes to content generation, quality, learning, or Facebook API behavior.
3. Merge only after CI is green and review is clean.
4. Confirm post-merge `main` CI.
5. Run production planner once if the current/future Vietnam day still lacks a persisted plan.
6. Run a controlled dispatcher wake and inspect `daily_plan`, `job_runs`, and `content_posts`.
7. Re-run Production Readiness.
8. Operational Phase 4 closure requires no `FAILED` liveness check.

## Out of scope

- changing target daily volume or category mix.
- changing quality/dedup thresholds.
- catch-up publishing multiple expired slots.
- adding Redis/Celery/VPS.
- adding an external scheduler in this P0.
- guaranteeing all 12 planned posts if GitHub Actions does not create enough wake-ups.
