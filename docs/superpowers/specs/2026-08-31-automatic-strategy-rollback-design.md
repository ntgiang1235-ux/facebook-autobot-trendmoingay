# Phase 4M — Automatic Strategy Rollback Design

Date: 2026-08-31
Status: Approved continuation of Phase 4 Adaptive Content Intelligence
Branch: `codex/phase-4m-automatic-strategy-rollback`

## Goal

Add a deterministic daily strategy guard that can detect sustained adaptive-performance regression from mature Facebook metrics and restore the last known-good strategy weights without rolling back application code.

## Fixed decisions

- Decision logic is deterministic Python. Gemini never decides rollback state.
- Compare two adjacent 7-day mature publication cohorts using canonical `final` `content_score` only.
- To avoid penalizing posts that have not reached the 72-hour final-metric horizon, the recent cohort ends 72 hours before guard execution; the prior cohort is the immediately preceding 7-day interval.
- Only published adaptive posts with a non-null `strategy_version` are eligible. Manual/baseline-isolated content is excluded from rollback evidence.
- Each cohort needs at least 5 final samples.
- Metric coverage for each cohort must be at least 80%: final-metric rows divided by eligible published adaptive posts in that cohort.
- Missing/degraded metric coverage causes a safe skip, never a rollback.
- A regression is decisive only when recent average final `content_score` is strictly more than 20% below the prior cohort average.
- A non-regressed, healthy comparison may promote the current strategy version to `last_good_strategy_version`.
- Rollback restores weights from the snapshot pointed to by `last_good_strategy_version` and creates a new append-only strategy version whose reason records the rollback target.
- Current safety statuses remain authoritative: retired or suspended values are not resurrected by weight rollback.
- Values absent from the last-good snapshot receive weight 0 during rollback.
- `adaptive_enabled=false` disables automatic rollback decisions while analytics remain available.

## Last-known-good semantics

`feedback_loop.refresh_strategy()` must stop automatically making every newly-created daily strategy version last-good. It only advances `current_strategy_version`.

The strategy guard owns `last_good_strategy_version`:

1. If there is no current strategy, skip.
2. If evidence is insufficient or metric coverage is degraded, preserve the existing last-good pointer.
3. If the comparison is healthy and not regressed, set `last_good_strategy_version = current_strategy_version`.
4. If regression exceeds 20% and a valid last-good snapshot exists, restore its weights and create a new rollback strategy version.
5. The rollback version becomes `current_strategy_version`; `last_good_strategy_version` continues to point at the proven source version.

The existing `strategy_versions` table stays append-only. `adaptive_config.last_good_strategy_version` is the canonical pointer; historical `is_last_good` flags are not mutated.

## Performance evidence

For an execution time `now`:

```text
recent_end   = now - 72 hours
recent_start = recent_end - 7 days
prior_end    = recent_start
prior_start  = prior_end - 7 days
```

For each interval, load:

- eligible adaptive published post count;
- count with canonical final score;
- average final score.

Coverage is `final_count / eligible_count`. A cohort with zero eligible posts, fewer than 5 final scores, or coverage below 0.80 cannot drive rollback.

If the prior average is zero or unavailable, rollback is skipped because a meaningful percentage regression cannot be computed.

## Rollback application

The last-good `StrategySnapshot.weights` remains the source of truth for rollback weights.

For every current `strategy_stats` row:

- if status is `retired` or `suspended`, keep weight 0;
- otherwise restore the snapshot weight for `(dimension, value)` when present;
- otherwise set weight 0.

After applying weights:

- allocate the next strategy version ID;
- save a new `StrategySnapshot` with reason `automatic rollback to v<target>`;
- save config with `current_strategy_version=<rollback version>` and unchanged `last_good_strategy_version=<target>`.

No schema-destructive migration or new external dependency is required.

## Hardened daily orchestration

Add a hardened `strategy_guard` action.

Production schedule order:

1. `learn` at 00:27 UTC;
2. `strategy_guard` at 00:32 UTC;
3. weekly `style_evolve` at 00:37 UTC on Sunday;
4. `planner` at 00:47 UTC.

The guard runs after learning so the latest candidate strategy exists, but before planning so a rollback changes the strategy version used by the day's plan.

Job outcomes:

- insufficient data / disabled adaptive / degraded metrics / no last-good target: `skipped`;
- healthy comparison with no state change: `success`;
- promotion of current version to last-good: `success`;
- rollback: `success` plus a best-effort Telegram alert;
- database/state corruption or unknown status: fail non-zero through the existing hardening contract.

Telegram alert failure is observability failure and must not undo or repeat a successfully persisted rollback.

## Testing

TDD coverage must prove:

- cohort boundaries are adjacent and delayed by 72 hours;
- manual or missing-strategy posts do not count;
- minimum 5 final samples per cohort;
- 80% coverage threshold is fail-safe;
- exactly 20% regression does not rollback, while greater than 20% does;
- healthy evidence promotes current to last-good;
- feedback refresh preserves last-good instead of overwriting it;
- rollback restores known weights, zeros unknown values, and does not revive suspended/retired values;
- rollback creates a new append-only strategy version and preserves the target last-good pointer;
- hardening runner and workflow schedule `strategy_guard` between learn and planner;
- full unit suite and compile gate remain green.
