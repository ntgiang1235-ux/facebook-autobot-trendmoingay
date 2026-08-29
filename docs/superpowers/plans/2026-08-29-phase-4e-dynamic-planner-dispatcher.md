# Phase 4E — Dynamic Planner and 30-Minute Dispatcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the business schedule from fixed per-action cron entries to a Turso-backed daily plan that can adapt category/time allocation while GitHub Actions wakes a safe dispatcher every 30 minutes.

**Architecture:** Add `daily_plan` persistence, a deterministic planner consuming Phase 4D strategy state, and a dispatcher that atomically claims due slots and executes them through `hardening_runner.run_action()`. Rollout is feature-flagged: existing fixed schedules remain available until dynamic scheduling is proven in dry-run/shadow mode.

**Tech Stack:** Python 3, Turso/libSQL, GitHub Actions cron, existing `hardening_runner`, `app.scheduler`, `unittest`.

**Spec:** `docs/superpowers/specs/2026-08-29-adaptive-content-intelligence-design.md`

## Global Constraints

- Requires 4A–4D merged.
- `daily_plan` is the business schedule source of truth only when `auto_schedule_enabled=true` and rollout mode permits it.
- GitHub wakes the dispatcher every 30 minutes; the dispatcher does not publish unless a due slot is atomically claimed.
- Total planned daily volume is approximately ±20% from configured baseline.
- Quality gate may cause actual published count below planned minimum.
- Existing stale-run protection must remain effective.
- Concurrent/retried workflows must not publish the same slot twice.
- Production cutover must use shadow mode before disabling fixed schedules.

---

## File Map

- Create `app/planner.py`: next-day volume/slot/category planning.
- Create `app/plan_repository.py`: `daily_plan` persistence and atomic claim.
- Create `app/dispatcher.py`: due-slot dispatch service.
- Modify `app/db.py`: add `daily_plan` schema/indexes.
- Modify `hardening_runner.py`: optional context parameters for planned slot/run key without breaking existing CLI.
- Create `dispatcher_runner.py`: CLI entry point.
- Modify `.github/workflows/facebook-autobot.yml`: add 30-minute dispatcher schedule and rollout switch; preserve fixed schedules during shadow stage.
- Create `tests/test_planner.py`, `tests/test_plan_repository.py`, `tests/test_dispatcher.py`.
- Modify `tests/test_hardening_runner.py`.
- Modify `tests/test_workflows.py` for dispatcher/cutover workflow assertions.

### Task 1: Add daily plan schema and repository

**Files:**
- Modify: `app/db.py`
- Create: `app/plan_repository.py`
- Test: `tests/test_plan_repository.py`
- Modify: `tests/test_db.py`

**Interfaces:**
- Produces `PlanSlot`, `save_plan`, `list_due_slots`, `claim_slot`, `finish_slot`, `get_plan_for_date`.

- [ ] **Step 1: Write failing schema/repository tests**

Require `daily_plan(id, plan_date, slot_time, category, action, strategy_mode, strategy_version, status, reason, created_at, claimed_at, completed_at)` and indexes on `(plan_date,status,slot_time)` plus uniqueness preventing duplicate logical slots for the same plan date/time/action.

Test `claim_slot()` returns `True` only when conditional update changes one `planned` row; a second claimant returns `False`.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_plan_repository tests.test_db -v`

Expected: FAIL for missing schema/module.

- [ ] **Step 3: Implement repository**

Use a conditional update equivalent to:

```sql
UPDATE daily_plan
SET status='running', claimed_at=?
WHERE id=? AND status='planned'
```

Determine success from a follow-up state read or driver rowcount supported by the repository abstraction; do not assume success merely because SQL executed.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_plan_repository tests.test_db -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/db.py app/plan_repository.py tests/test_db.py tests/test_plan_repository.py
git commit -m "feat: add atomic daily plan storage"
```

### Task 2: Implement adaptive daily volume and slot planning

**Files:**
- Create: `app/planner.py`
- Test: `tests/test_planner.py`

**Interfaces:**
- Produces `volume_bounds(baseline: int) -> tuple[int,int]`, `build_daily_plan(plan_date, config, category_stats, time_stats, rng) -> list[PlannedSlot]`.

- [ ] **Step 1: Write failing planner tests**

For baseline 12 assert integer bounds 10–14 using deterministic rounding rules `ceil(0.8*baseline)` and `floor(1.2*baseline)`. Assert suspended categories get zero normal slots; a due re-test gets at most one `retest` slot; exploit/explore mode is attached; duplicate times are not emitted; empty/weak options fall back to configured baseline categories/times rather than producing invalid rows.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_planner -v`

Expected: import failure.

- [ ] **Step 3: Implement planner**

Planner selects desired volume within bounds from recent overall score trend, but caps day-to-day volume change to the same ±20% envelope. It uses Phase 4D selectors for category/time allocation and assigns action mapping explicitly, e.g. `news -> post`, `finance -> finance`, `philosophy -> philosophy`, `recipe -> recipe`, `fun -> fun`, `video -> video`. Keep `reply`, `health`, `metrics`, reporting jobs outside adaptive content-volume accounting.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_planner -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/planner.py tests/test_planner.py
git commit -m "feat: build adaptive daily plans"
```

### Task 3: Implement due-slot freshness and atomic dispatcher

**Files:**
- Create: `app/dispatcher.py`
- Test: `tests/test_dispatcher.py`

**Interfaces:**
- Produces `dispatch_due_slots(now, execute_fn, run_action_fn, stale_after_minutes=60) -> DispatchSummary`.

- [ ] **Step 1: Write failing dispatcher tests**

Cover no due slots => no-op success; due slot claim success => action called once; second concurrent claim => no action; slot >60 minutes late => marked skipped with `stale schedule`; action success => `published/done`; action skipped => `skipped`; action exception => `failed` and re-raised after slot state is persisted; unrelated later due slots are not double-run by the same claimed id.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_dispatcher -v`

Expected: import failure.

- [ ] **Step 3: Implement dispatcher**

Use timezone-aware UTC datetimes. Keep business-slot freshness separate from GitHub wake-up cron metadata. `DispatchSummary` contains counts `due`, `claimed`, `completed`, `skipped`, `failed` for Telegram/reporting later.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_dispatcher -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/dispatcher.py tests/test_dispatcher.py
git commit -m "feat: atomically dispatch adaptive slots"
```

### Task 4: Wire dispatcher through hardened production actions

**Files:**
- Create: `dispatcher_runner.py`
- Modify: `hardening_runner.py`
- Modify: `tests/test_hardening_runner.py`
- Modify: `tests/test_dispatcher.py`

**Interfaces:**
- `dispatcher_runner.main()` invokes `db.ensure_schema()` then `dispatch_due_slots(..., run_action_fn=hardening_runner.run_action)`.
- `hardening_runner.run_action()` remains callable exactly as before by current workflows.

- [ ] **Step 1: Write failing integration tests**

Mock a claimed plan slot with action `finance`; assert dispatcher invokes hardened `run_action("finance")`, not `runner.financial_post_job` directly. Assert existing direct calls `run_action("health")` and current CLI usage still work.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_dispatcher tests.test_hardening_runner -v`

Expected: FAIL because runner integration is absent.

- [ ] **Step 3: Implement minimal wiring**

Do not duplicate config injection from `resolve_jobs()`. Dispatcher passes only valid actions and lets `hardening_runner` own runtime validation, DB job recording, notifier behavior, and production adapters.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_dispatcher tests.test_hardening_runner -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dispatcher_runner.py hardening_runner.py tests/test_dispatcher.py tests/test_hardening_runner.py
git commit -m "feat: route adaptive dispatcher through hardened runner"
```

### Task 5: Add GitHub Actions dispatcher in shadow mode

**Files:**
- Modify: `.github/workflows/facebook-autobot.yml`
- Modify: `tests/test_workflows.py`
- Modify: `dispatcher_runner.py`

**Interfaces:**
- GitHub cron: `*/30 * * * *`.
- Environment rollout control: `ADAPTIVE_DISPATCH_MODE=shadow|active`, default `shadow` during first deployment.

- [ ] **Step 1: Write failing workflow static tests**

Add tests to `tests/test_workflows.py` that load `.github/workflows/facebook-autobot.yml` using the existing `ROOT` pattern and assert: one `cron: "*/30 * * * *"` exists; `dispatcher_runner.py` is referenced; fixed production content crons remain during shadow mode; `ADAPTIVE_DISPATCH_MODE` is present; and shadow mode is explicit rather than inferred.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_workflows -v`

Expected: FAIL because dispatcher cron/config is absent.

- [ ] **Step 3: Modify workflow and runner mode**

In shadow mode, build/read the proposed plan and log/report what would be due but never call `claim_slot()` or `run_action()`. Keep health, reply, metrics, and fixed publishing schedules unchanged at this checkpoint.

- [ ] **Step 4: Full verification**

```bash
python -m unittest tests.test_workflows tests.test_dispatcher -v
python -m unittest discover -s tests -v
python -m compileall -q .
git diff --check
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/facebook-autobot.yml dispatcher_runner.py tests/test_workflows.py
git commit -m "ci: add adaptive dispatcher shadow schedule"
```

### Task 6: Controlled production cutover after shadow evidence

**Files:**
- Modify: `.github/workflows/facebook-autobot.yml`
- Modify: `tests/test_workflows.py`

**Interfaces:**
- Active dispatcher publishes adaptive slots.
- Old fixed content crons are removed only after shadow verification; health/reply/metrics/reporting schedules remain explicit.

- [ ] **Step 1: Add cutover tests**

Extend `tests/test_workflows.py` to assert active-mode workflow cannot contain the old adaptive-content fixed cron mappings in a way that can double-publish, while health/reply and non-content actions remain scheduled/manual as designed.

- [ ] **Step 2: Verify tests fail against shadow config**

Run: `python -m unittest tests.test_workflows -v`

Expected: RED because fixed content crons still exist.

- [ ] **Step 3: Perform cutover config**

Remove only fixed content publishing schedules replaced by `daily_plan`; retain manual `workflow_dispatch` choices and non-adaptive operational actions. Set active mode through the explicit rollout configuration defined in this plan.

- [ ] **Step 4: Verify full suite and inspect workflow diff**

Run:

```bash
python -m unittest tests.test_workflows -v
python -m unittest discover -s tests -v
python -m compileall -q .
git diff --check
```

Then manually confirm there is exactly one automated path capable of publishing each adaptive slot.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/facebook-autobot.yml tests/test_workflows.py
git commit -m "ci: activate adaptive content dispatcher"
```

## 4E Acceptance Gate

Do not activate production scheduling until shadow output matches expected plans for at least one full daily cycle. Required evidence: atomic claim test, stale skip test, hardened runner routing, no duplicate publishing path, ±20% plan volume, suspended/retest handling, and clean rollback path via `auto_schedule_enabled=false` / rollout mode.
