# Phase 4N.2 Operational Liveness Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent a missed GitHub planner wake from causing a zero-post day, use every scheduled wake as one safe dispatch opportunity, and make production readiness fail on publishing-loop liveness outages.

**Architecture:** Keep the existing adaptive planner and atomic `daily_plan` dispatcher as the only scheduling authority. Add an idempotent `ensure_daily_plan()` self-heal before dispatch, add one opportunistic dispatcher attempt to non-dispatch GitHub `schedule` runs, and add a strictly read-only liveness check to `app/readiness.py`.

**Tech Stack:** Python 3.11, `unittest`, GitHub Actions, Turso/libSQL, existing `JobOutcome`/`run_job` contract.

**Spec:** `docs/superpowers/specs/2026-08-31-operational-liveness-recovery-design.md`

## Global Constraints

- No second scheduling authority and no restoration of fixed category publish crons.
- No catch-up flood: at most one currently due slot per scheduled wake.
- Existing dispatcher grace remains 20 minutes; expired slots are never backfilled.
- Manual `workflow_dispatch` actions must not publish implicitly.
- Explicit `dispatch` must not run twice.
- Readiness stays strictly read-only and uses no Facebook/Gemini/Pexels/Telegram calls.
- Existing dedup, quality, learning, style, strategy and Facebook publish behavior are unchanged.
- No external scheduler is added in this P0.

---

### Task 1: Self-Healing Daily Plan Before Dispatch

**Files:**
- Modify: `app/adaptive_jobs.py`
- Modify: `app/dispatcher.py`
- Modify: `tests/test_adaptive_jobs.py`
- Modify: `tests/test_dispatcher.py`

**Interfaces:**
- Produces: `adaptive_jobs.ensure_daily_plan(execute_fn, now: datetime | None = None) -> JobOutcome`.
- Consumes: existing `plan_repository.list_slots()`, `create_daily_plan()`, `planner.build_daily_plan()`, and atomic `claim_due_slot()`.
- Dispatcher calls `ensure_daily_plan(execute_fn, now=current)` before `claim_due_slot()`.

- [ ] **Step 1: Write RED tests for idempotent plan guarantee**

Add tests proving `ensure_daily_plan()` returns skipped/no-op when `list_slots()` already has rows, calls `create_daily_plan()` exactly once when empty, and uses the Vietnam-local date.

```python
with patch.object(adaptive_jobs.plan_repository, "list_slots", return_value=[existing]), patch.object(
    adaptive_jobs, "create_daily_plan"
) as create:
    outcome = adaptive_jobs.ensure_daily_plan(execute, now=now)
self.assertEqual(outcome.status, "skipped")
create.assert_not_called()
```

```python
with patch.object(adaptive_jobs.plan_repository, "list_slots", return_value=[]), patch.object(
    adaptive_jobs, "create_daily_plan", return_value=success("planned 12 slots")
) as create:
    outcome = adaptive_jobs.ensure_daily_plan(execute, now=now)
create.assert_called_once_with(execute, now=now)
```

- [ ] **Step 2: Write RED dispatcher test for self-heal before claim**

Patch `dispatcher.adaptive_jobs.ensure_daily_plan` and `claim_due_slot`; assert the guarantee is called before claim and a currently due slot can still publish. Also retain/extend the existing late-slot test so slots outside the 20-minute grace expire instead of publishing.

- [ ] **Step 3: Run focused tests and confirm RED**

Run: `python -m unittest tests.test_adaptive_jobs tests.test_dispatcher -v`

Expected: new tests fail because `ensure_daily_plan` does not exist / dispatcher does not call it.

- [ ] **Step 4: Implement minimal self-heal**

In `app/adaptive_jobs.py`:

```python
def ensure_daily_plan(execute_fn, now: datetime | None = None) -> JobOutcome:
    current = _as_utc(now)
    plan_date = current.astimezone(VIETNAM_TZ).date().isoformat()
    if plan_repository.list_slots(execute_fn, plan_date):
        return skipped(f"plan already exists for {plan_date}")
    return create_daily_plan(execute_fn, now=current)
```

Import `skipped` alongside `success`.

In `app/dispatcher.py`, import `adaptive_jobs` and call:

```python
adaptive_jobs.ensure_daily_plan(execute_fn, now=current)
```

immediately before `claim_due_slot()`.

- [ ] **Step 5: Run focused tests and confirm GREEN**

Run: `python -m unittest tests.test_adaptive_jobs tests.test_dispatcher -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/adaptive_jobs.py app/dispatcher.py tests/test_adaptive_jobs.py tests/test_dispatcher.py
git commit -m "fix: self-heal missing daily plans before dispatch"
```

---

### Task 2: Opportunistic Dispatch on Every Scheduled Wake

**Files:**
- Modify: `hardening_runner.py`
- Modify: `tests/test_hardening_runner.py`
- Modify: `tests/test_adaptive_stale_tolerance.py`

**Interfaces:**
- Produces: `_run_opportunistic_dispatch(jobs: dict[str, Callable], *, parent_run_key: str, scheduled_for: str | None, delay_minutes: int | None) -> JobOutcome`.
- Uses: `dispatcher.dispatch_due(db.execute, adaptive_content_jobs, run_key=<distinct key>)` and existing `run_job()` observability contract.
- Trigger condition: `GITHUB_EVENT_NAME == "schedule" and action != "dispatch"`.

- [ ] **Step 1: Write RED tests for scheduled-only side effect**

Add tests proving:

```python
# schedule + health => one opportunistic dispatch then health
# workflow_dispatch + health => no implicit dispatch
# schedule + explicit dispatch => exactly one dispatch
```

Use injected jobs for adaptive content actions plus the primary action, patch `dispatcher.dispatch_due`, and assert the opportunistic run key differs from the primary run key.

- [ ] **Step 2: Write RED stale-path test**

For a stale scheduled maintenance or publish action, assert opportunistic dispatch is attempted before the primary stale return. Keep the existing assertion that the stale publish job itself is not executed.

- [ ] **Step 3: Run focused tests and confirm RED**

Run: `python -m unittest tests.test_hardening_runner tests.test_adaptive_stale_tolerance -v`

Expected: only new opportunistic-dispatch assertions fail.

- [ ] **Step 4: Implement one dispatch attempt with independent observability**

Add helper logic equivalent to:

```python
def _run_opportunistic_dispatch(jobs, *, parent_run_key, scheduled_for, delay_minutes):
    run_key = f"{parent_run_key}-opportunistic-dispatch"
    started_at = utc_now_iso()
    db.record_job(run_key, "dispatch", "started", started_at, None, "", scheduled_for, delay_minutes)
    adaptive_content_jobs = {action: jobs[action] for action in ADAPTIVE_CONTENT_ACTIONS}

    def recorder(status, detail=""):
        db.record_job(run_key, "dispatch", status, started_at, utc_now_iso(), detail[:1000], scheduled_for, delay_minutes)

    def notifier(_, error):
        notifications.send_failure("dispatch", error, github_run_url())

    return run_job(
        "dispatch",
        lambda: dispatcher.dispatch_due(db.execute, adaptive_content_jobs, run_key=run_key),
        recorder,
        notifier,
    )
```

In `run_action()`, after schema + primary `started` record and before the stale early-return:

```python
if os.getenv("GITHUB_EVENT_NAME") == "schedule" and action != "dispatch":
    _run_opportunistic_dispatch(...)
```

Do not trigger this path for manual runs.

- [ ] **Step 5: Run focused tests and confirm GREEN**

Run: `python -m unittest tests.test_hardening_runner tests.test_adaptive_stale_tolerance -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add hardening_runner.py tests/test_hardening_runner.py tests/test_adaptive_stale_tolerance.py
git commit -m "fix: use scheduled wakes as dispatch opportunities"
```

---

### Task 3: Read-Only Operational Liveness Gate

**Files:**
- Modify: `app/readiness.py`
- Modify: `tests/test_readiness.py`
- Modify: `tests/test_readiness_shadow_plan.py` only if shared fixtures need valid liveness state.

**Interfaces:**
- Produces: `_liveness_check(execute_fn, config: AdaptiveConfig, now: datetime) -> ReadinessCheck`.
- `run_readiness()` appends liveness before learning/shadow plan; aggregate semantics remain `FAILED > DEGRADED > READY`.
- Constants: first safe slot `08:30` Vietnam, dispatcher freshness threshold `90` minutes, dispatcher grace `20` minutes, zero-publication threshold `3` elapsed slots.

- [ ] **Step 1: Add deterministic liveness fixture helpers**

Use SQLite/in-memory executor patterns already present in `tests/test_readiness.py`. Populate only raw `daily_plan`, `job_runs`, and `content_posts` state needed for each case.

- [ ] **Step 2: Write RED liveness tests**

Cover exactly:

```python
# 08:00 VN, adaptive scheduling enabled, no plan => DEGRADED
# 09:00 VN, no plan => FAILED
# plan exists, latest dispatch older than 90 minutes => FAILED
# >=3 slots older than 20-minute grace and 0 published rows today => FAILED
# plan exists + recent dispatch + >=1 published row => READY
# learning DEGRADED plus liveness FAILED => aggregate FAILED
```

- [ ] **Step 3: Run focused readiness tests and confirm RED**

Run: `python -m unittest tests.test_readiness tests.test_readiness_shadow_plan -v`
Expected: new liveness tests fail because no liveness check exists.

- [ ] **Step 4: Implement `_liveness_check()` using static SELECTs only**

The function must:

1. Return READY immediately when adaptive or auto-schedule is disabled.
2. Compute Vietnam date/day-start and current local time from injected `now`.
3. Query today's `daily_plan` rows.
4. Missing plan: DEGRADED before 08:30, FAILED at/after 08:30.
5. Query latest `job_runs.started_at` for `action='dispatch'`; after day start, missing or older than 90 minutes => FAILED.
6. Parse `planned_for`; count slots with `planned_for < now - 20 minutes`.
7. If at least 3 such slots exist, query today's `content_posts` with `status='published'` and non-null `facebook_post_id`; zero => FAILED.
8. Otherwise return READY with plan count, recent dispatcher timestamp, and publication count.

All SQL remains plain `SELECT`; no schema or state mutation.

- [ ] **Step 5: Wire liveness into `run_readiness()`**

After config is validated and before learning/shadow-plan aggregation:

```python
current = _as_utc(now)
checks.append(_liveness_check(execute_fn, config, current))
```

If liveness fails, it may still be useful to complete shadow-plan diagnostics, but the final result must remain FAILED.

- [ ] **Step 6: Run focused tests and confirm GREEN**

Run: `python -m unittest tests.test_readiness tests.test_readiness_shadow_plan -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/readiness.py tests/test_readiness.py tests/test_readiness_shadow_plan.py
git commit -m "feat: fail readiness on publishing liveness outages"
```

---

### Task 4: Full Verification, Review, and Rollout Gate

**Files:**
- Review only: `.github/workflows/facebook-autobot.yml`
- Review all changed files from Tasks 1-3.

**Interfaces:** No new production interface. This task validates scope and safety.

- [ ] **Step 1: Run complete unit suite**

Run: `python -m unittest discover -s tests -v`
Expected: all tests PASS.

- [ ] **Step 2: Compile all Python sources**

Run: `python -m compileall -q .`
Expected: exit 0.

- [ ] **Step 3: Review diff scope**

Confirm no changes to content generation prompts, quality thresholds, dedup thresholds, learning weights, strategy rollback thresholds, Facebook API endpoints, target daily volume, or GitHub cron expressions.

- [ ] **Step 4: Open PR and wait for CI**

PR title: `fix: recover operational publishing liveness`

CI acceptance: full unit suite + compileall green.

- [ ] **Step 5: Merge only after explicit approval**

Do not merge automatically after CI. Present PR/CI evidence and request merge approval.

- [ ] **Step 6: Post-merge production validation**

After merge approval and green `main` CI:

1. Read production `daily_plan` for current/next Vietnam date.
2. If the active publishing day has no plan, run one controlled `planner` action through the normal production runner.
3. Run one controlled `dispatch` wake only if a slot is currently inside the existing 20-minute grace window; do not force an expired/future slot.
4. Inspect `daily_plan`, `job_runs`, and `content_posts` read-only.
5. Re-run Production Readiness.
6. Operational closure requires no `FAILED` liveness check; cold-start learning may remain `DEGRADED`.
