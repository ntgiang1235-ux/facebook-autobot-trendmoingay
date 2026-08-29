# Phase 4G — Safety, Rollback, and Production Learning Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the adaptive system can fail safely, roll back strategy state without rolling back code, detect material regressions, and operate end-to-end in production with auditable evidence.

**Architecture:** Add explicit kill-switch resolution, versioned strategy snapshots, a deterministic regression detector, and production verification jobs that observe first and mutate only strategy state when all rollback conditions are met. Keep code rollback and strategy rollback separate.

**Tech Stack:** Python 3, Turso/libSQL, existing Telegram/GitHub Actions hardening, `unittest`.

**Spec:** `docs/superpowers/specs/2026-08-29-adaptive-content-intelligence-design.md`

## Global Constraints

- Requires 4A–4F merged and green.
- Kill switches: `adaptive_enabled`, `auto_schedule_enabled`, `auto_suspend_enabled`.
- Every published adaptive post must carry a durable `strategy_version`.
- Automatic strategy rollback trigger: recent 7-day composite performance regresses by more than 20%, minimum mature samples are met, metric health is not degraded, and regression is attributable to strategy rather than missing data.
- Rollback changes strategy state only; it must never rewrite application code or Git history.
- Metric/API degradation must disable unsafe regression conclusions.
- Existing observability failures must not mask original business failures.

---

## File Map

- Create `app/safety.py`: kill-switch policy and fallback strategy resolution.
- Create `app/regression.py`: regression eligibility/detection.
- Modify `app/strategy_repository.py`: last-good snapshot promotion/rollback transaction.
- Create `verification_runner.py`: read-only/shadow and live verification commands.
- Modify `hardening_runner.py`: add `verify_adaptive` and `strategy_guard` actions.
- Modify `.github/workflows/facebook-autobot.yml`: recurring strategy guard and manual verification action.
- Create `tests/test_safety.py`, `tests/test_regression.py`, `tests/test_verification_runner.py`.
- Modify `tests/test_strategy_repository.py`, `tests/test_hardening_runner.py`, `tests/test_notifications.py`, `tests/test_workflows.py`.

### Task 1: Implement kill-switch precedence and baseline fallback

**Files:**
- Create: `app/safety.py`
- Test: `tests/test_safety.py`

**Interfaces:**
- Produces `EffectiveMode`, `resolve_effective_mode(config) -> EffectiveMode`.

- [ ] **Step 1: Write failing precedence tests**

Assert:
- `adaptive_enabled=false` => baseline/static strategy, no adaptive weight application, no auto-suspend;
- `adaptive_enabled=true` + `auto_schedule_enabled=false` => adaptive analytics/strategy may compute, but fixed/baseline schedule is used;
- `auto_suspend_enabled=false` => weak categories remain eligible even if learning suggests suspension;
- all true => full adaptive mode.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_safety -v`

Expected: import failure.

- [ ] **Step 3: Implement explicit mode resolver**

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class EffectiveMode:
    use_adaptive_strategy: bool
    use_dynamic_schedule: bool
    allow_auto_suspend: bool
    reason: str


def resolve_effective_mode(config) -> EffectiveMode:
    if not config.adaptive_enabled:
        return EffectiveMode(False, False, False, "adaptive_disabled")
    return EffectiveMode(
        True,
        bool(config.auto_schedule_enabled),
        bool(config.auto_suspend_enabled),
        "adaptive_enabled",
    )
```

No environment variable silently overrides durable config except the explicit deployment shadow/active gate defined in 4E.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_safety -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/safety.py tests/test_safety.py
git commit -m "feat: add adaptive kill switch policy"
```

### Task 2: Make strategy version snapshots rollback-safe

**Files:**
- Modify: `app/strategy_repository.py`
- Modify: `tests/test_strategy_repository.py`

**Interfaces:**
- Consumes Phase 4D `load_config(execute_fn)`, `save_config(execute_fn, config)`, `save_strategy_version(...)`, `load_strategy_version(execute_fn, version_id)`.
- Produces `promote_last_good(execute_fn, version_id)`, `rollback_to_version(execute_fn, version_id) -> int`, `current_strategy_version(execute_fn) -> int`.

- [ ] **Step 1: Write failing versioning tests**

Assert creating a new version does not overwrite prior snapshot; `promote_last_good(v17)` marks exactly one last-good version; rollback from v18 to v17 creates a new version v19 whose payload equals v17 and whose reason records `automatic_rollback_from:18`; posts already tagged v18 remain historically unchanged.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_strategy_repository -v`

Expected: FAIL for missing rollback functions.

- [ ] **Step 3: Implement append-only rollback semantics**

```python
from dataclasses import replace


def rollback_to_version(execute_fn, version_id: int) -> int:
    source = load_strategy_version(execute_fn, version_id)
    config = load_config(execute_fn)
    current = config.current_strategy_version
    new_version = save_strategy_version(
        execute_fn,
        source.payload,
        reason=f"automatic_rollback_from:{current}",
        is_last_good=False,
    )
    save_config(
        execute_fn,
        replace(config, current_strategy_version=new_version),
    )
    return new_version
```

Never update old snapshot payloads. If copy/write fails, keep the existing current version and raise; implement the write sequence with the strongest transaction behavior supported by the existing libSQL abstraction.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_strategy_repository -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/strategy_repository.py tests/test_strategy_repository.py
git commit -m "feat: add append only strategy rollback"
```

### Task 3: Implement regression detector with data-health guard

**Files:**
- Create: `app/regression.py`
- Test: `tests/test_regression.py`

**Interfaces:**
- Produces `RegressionInput`, `RegressionDecision`, `evaluate_regression(data) -> RegressionDecision`.

- [ ] **Step 1: Write failing detector tests**

Cases:
- 19% decline => no rollback;
- 21% decline with enough mature samples and healthy metrics => rollback eligible;
- 30% decline but fewer than 5 mature posts => no rollback;
- 30% decline with degraded reach/impressions capability versus baseline period => no automatic rollback, status `metrics_degraded`;
- 30% decline while strategy version did not materially change => no strategy-attribution rollback;
- divide-by-zero/empty baseline => insufficient data, never rollback.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_regression -v`

Expected: import failure.

- [ ] **Step 3: Implement exact trigger**

```python
def evaluate_regression(data: RegressionInput) -> RegressionDecision:
    if data.baseline_score <= 0 or data.mature_samples < 5:
        return RegressionDecision(False, 0.0, "insufficient_data", None)
    if data.metric_capabilities != data.baseline_metric_capabilities:
        return RegressionDecision(False, 0.0, "metrics_degraded", None)
    decline = (data.baseline_score - data.recent_score) / data.baseline_score
    if decline <= 0.20:
        return RegressionDecision(False, decline, "within_guardrail", None)
    if not data.strategy_changed:
        return RegressionDecision(False, decline, "not_attributable_to_strategy", None)
    return RegressionDecision(True, decline, "regression_detected", data.last_good_version)
```

The comparison uses `>0.20`, not `>=0.20`.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_regression -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/regression.py tests/test_regression.py
git commit -m "feat: detect adaptive strategy regressions"
```

### Task 4: Add strategy guard job and Telegram alert

**Files:**
- Create: `verification_runner.py`
- Modify: `hardening_runner.py`
- Modify: `app/notifications.py`
- Create: `tests/test_verification_runner.py`
- Modify: `tests/test_hardening_runner.py`
- Modify: `tests/test_notifications.py`

**Interfaces:**
- Produces `load_regression_input(execute_fn, now) -> RegressionInput`.
- Produces `run_strategy_guard(dry_run: bool = False) -> GuardResult` and hardened action `strategy_guard`.

- [ ] **Step 1: Write failing guard tests**

Assert healthy/no-regression => no mutation; eligible regression in dry-run => reports intended rollback but does not mutate; eligible regression live => `rollback_to_version()` exactly once then Telegram alert includes old/current/new strategy versions and decline percentage; rollback DB failure => job fails and alert says rollback was not completed; Telegram alert failure does not fake a successful rollback state.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_verification_runner tests.test_hardening_runner tests.test_notifications -v`

Expected: FAIL because guard is missing.

- [ ] **Step 3: Implement guard and loader**

`load_regression_input()` reads the current strategy version, last-good version, comparable 7-day recent/baseline final-score windows, mature sample count, and metric capability sets from Phase 4C/4D repositories. It returns one `RegressionInput`; no decision is made inside the loader.

```python
def run_strategy_guard(dry_run: bool = False):
    now = datetime.now(timezone.utc)
    data = load_regression_input(db.execute, now)
    decision = regression.evaluate_regression(data)
    if not decision.rollback:
        return GuardResult("ok", decision.reason, None)
    if dry_run:
        return GuardResult("would_rollback", decision.reason, decision.target_version)
    new_version = strategy_repository.rollback_to_version(db.execute, decision.target_version)
    notifications.send_strategy_alert(
        data.current_version,
        new_version,
        decision.decline_ratio,
    )
    return GuardResult("rolled_back", decision.reason, new_version)
```

Wire through `hardening_runner` so `job_runs` records the outcome.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_verification_runner tests.test_hardening_runner tests.test_notifications -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add verification_runner.py hardening_runner.py app/notifications.py tests/test_verification_runner.py tests/test_hardening_runner.py tests/test_notifications.py
git commit -m "feat: guard and rollback adaptive strategy"
```

### Task 5: Add end-to-end adaptive verification command

**Files:**
- Modify: `verification_runner.py`
- Modify: `hardening_runner.py`
- Modify: `tests/test_verification_runner.py`

**Interfaces:**
- Produces private checks `check_schema`, `check_strategy_versions`, `check_daily_plan`, `check_metrics_maturity`, `check_strategy_weights`.
- Produces `verify_adaptive_state(execute_fn, now) -> VerificationReport`, hardened action `verify_adaptive`.

- [ ] **Step 1: Write failing verification tests**

Verification must check: schema/table presence; config switches readable; current and last-good strategy versions valid; current daily plan has no duplicate logical slots; no slot stuck `running` beyond stale threshold; published adaptive posts have `facebook_post_id` and `strategy_version`; 24h/72h metrics completeness for eligible posts; strategy weights finite/non-negative; active dimension weights normalize approximately to 1; suspended categories have retest date; workflow mode/config is coherent.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_verification_runner -v`

Expected: FAIL for missing verifier.

- [ ] **Step 3: Implement read-only verifier**

```python
def verify_adaptive_state(execute_fn, now) -> VerificationReport:
    checks = []
    checks.extend(check_schema(execute_fn))
    checks.extend(check_strategy_versions(execute_fn))
    checks.extend(check_daily_plan(execute_fn, now))
    checks.extend(check_metrics_maturity(execute_fn, now))
    checks.extend(check_strategy_weights(execute_fn))
    return VerificationReport(tuple(checks))
```

Each private check returns named `VerificationCheck(status, name, detail)` records. `verify_adaptive` must not mutate production state. Fail the hardened job only when at least one check has status `FAIL`; warnings remain visible but non-fatal.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_verification_runner -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add verification_runner.py hardening_runner.py tests/test_verification_runner.py
git commit -m "feat: verify adaptive production state"
```

### Task 6: Schedule guard and expose manual verification

**Files:**
- Modify: `.github/workflows/facebook-autobot.yml`
- Modify: `tests/test_workflows.py`

**Interfaces:**
- Strategy guard cron: `45 15 * * *` UTC = 22:45 Vietnam.
- `verify_adaptive` remains manual through `workflow_dispatch`.

- [ ] **Step 1: Write failing workflow tests**

Extend `WorkflowTests` in `tests/test_workflows.py`:

```python
def test_adaptive_strategy_guard_schedule(self):
    prod = (ROOT / ".github/workflows/facebook-autobot.yml").read_text(encoding="utf-8")
    self.assertIn('cron: "45 15 * * *"', prod)
    self.assertIn('ACTION="strategy_guard"', prod)
    self.assertIn("verify_adaptive", prod)
```

Also assert the strategy-guard cron branch does not call `dispatcher_runner.py` or a publish action.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_workflows -v`

Expected: FAIL because mappings are absent.

- [ ] **Step 3: Add workflow mappings**

Add `45 15 * * *` and map it only to `strategy_guard`. Add `verify_adaptive` to manual action choices. Keep `strategy_guard` separate from the adaptive dispatcher so a dispatcher failure cannot disable rollback monitoring.

- [ ] **Step 4: Full repository verification**

Run:

```bash
python -m unittest tests.test_workflows -v
python -m unittest discover -s tests -v
python -m compileall -q .
git diff --check
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/facebook-autobot.yml tests/test_workflows.py
git commit -m "ci: schedule adaptive strategy guard"
```

### Task 7: Production learning verification sequence

**Files:**
- No source changes unless a verified defect is found; a defect gets its own failing test, minimal fix, focused verification, and commit before this sequence resumes.

**Interfaces:**
- Evidence checklist only.

- [ ] **Step 1: Pre-cutover dry-run evidence**

Run `verify_adaptive` and strategy guard dry-run against production. Require no FAIL; record warnings separately.

- [ ] **Step 2: Shadow-plan evidence**

Confirm one full daily cycle of dispatcher shadow decisions matches Turso plan and produces no Facebook publish calls from shadow mode.

- [ ] **Step 3: Active cutover evidence**

After 4E active mode is enabled, verify each due plan slot is claimed at most once and every adaptive publish stores `facebook_post_id` + `strategy_version`.

- [ ] **Step 4: Metrics maturity evidence**

After eligible posts pass 24h then 72h, verify canonical snapshots exist, capability metadata is truthful, and `final_score` is used preferentially by the learner.

- [ ] **Step 5: Learning evidence**

Require at least one strategy update after sufficient samples. Confirm daily movement cap, 80/20 mode labeling, and no category suspension before five mature posts.

- [ ] **Step 6: Rollback drill**

Using test/dry-run state, simulate >20% eligible regression and prove the guard selects the last-good strategy; do not intentionally degrade real Facebook content. If a disposable Turso/test DB is available, perform a live strategy-state rollback there and verify append-only version history.

- [ ] **Step 7: Final production verification**

Run full CI plus `verify_adaptive`; review Telegram daily/weekly output; confirm fixed publishing crons are not concurrently active with adaptive active mode; confirm repo branch rules/checks remain green.

## 4G Acceptance Gate

Phase 4 is complete only when code tests are green and production evidence proves: kill switches work, strategy versions are auditable, no duplicate slot execution, metrics degrade safely, learning waits for mature data, strategy movement is bounded, daily/weekly reports explain decisions, and automatic rollback can only fire under the approved >20% guarded condition.
