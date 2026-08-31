# Automatic Strategy Rollback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect >20% regression in mature adaptive Facebook performance, preserve a proven last-good strategy, and restore its weights before daily planning.

**Architecture:** Add a focused `strategy_guard` module that loads two adjacent 7-day mature cohorts, applies evidence/coverage gates, promotes healthy current strategy versions, or restores a last-good snapshot by creating a new rollback version. Modify feedback refresh so it no longer marks every daily version last-good, then wire the guard into the hardened runner and production workflow between learning and planning.

**Tech Stack:** Python 3.11, unittest, Turso/libSQL, GitHub Actions, Telegram notifications.

**Spec:** `docs/superpowers/specs/2026-08-31-automatic-strategy-rollback-design.md`

## Global Constraints

- Deterministic Python owns rollback decisions; Gemini cannot mutate strategy state.
- Two adjacent 7-day cohorts are anchored 72 hours behind execution time.
- Minimum 5 final samples per cohort.
- Minimum 80% final-metric coverage per cohort.
- Rollback threshold is strictly greater than 20% regression.
- Only adaptive published posts with non-null `strategy_version` count.
- No destructive schema migration and no new external dependency.
- Existing hardening semantics remain: state/database failures fail non-zero; observability failures do not undo a persisted rollback.

---

### Task 1: Strategy guard evidence and decision engine

**Files:**
- Create: `app/strategy_guard.py`
- Create: `tests/test_strategy_guard.py`

**Interfaces:**
- Produces: `StrategyGuardResult` and `run_strategy_guard(execute_fn, *, now=None) -> StrategyGuardResult`.
- Uses existing `load_config`, `save_config`, `load_stats`, `load_strategy_version`, `upsert_stat`, and `save_strategy_version` repository functions.

- [ ] **Step 1: Write failing evidence tests** for exact mature-window boundaries, adaptive-only filtering, minimum sample gate, and 80% coverage gate.
- [ ] **Step 2: Run `python -m unittest tests.test_strategy_guard -v` and verify RED** because `app.strategy_guard` does not exist.
- [ ] **Step 3: Implement cohort loading** with `recent_end = now - 72h`, two adjacent 7-day windows, one aggregate query per window, and a typed evidence dataclass.
- [ ] **Step 4: Write failing decision tests** proving 20% exactly is not rollback, >20% is rollback, disabled adaptive skips, missing last-good skips, and healthy evidence promotes current to last-good.
- [ ] **Step 5: Implement deterministic decision logic** without persistence shortcuts or Gemini calls.
- [ ] **Step 6: Write failing rollback persistence tests** proving known weights restore, missing values become zero, suspended/retired remain zero, a new version is appended, and the target last-good pointer is preserved.
- [ ] **Step 7: Implement minimal rollback persistence** using existing strategy snapshot/config storage and current strategy rows.
- [ ] **Step 8: Run `python -m unittest tests.test_strategy_guard -v` and verify GREEN.**
- [ ] **Step 9: Commit** with message `feat: add automatic strategy rollback guard`.

### Task 2: Preserve last-good across daily learning refresh

**Files:**
- Modify: `app/feedback_loop.py`
- Modify: `tests/test_feedback_idempotency.py`
- Modify: `tests/test_feedback_loop.py` only if snapshot assertions require it.

**Interfaces:**
- `refresh_strategy()` continues to create/repair `current_strategy_version` but must preserve `last_good_strategy_version` unchanged.

- [ ] **Step 1: Change tests first** so same-day config repair sets only `current_strategy_version=latest`, preserving the prior last-good pointer, and new daily snapshots do not overwrite last-good.
- [ ] **Step 2: Run focused feedback tests and verify RED** against the existing behavior that sets last-good to latest.
- [ ] **Step 3: Implement minimal feedback changes**: preserve `config.last_good_strategy_version` in same-day repair and new-version config; new adaptive snapshots are not automatically declared last-good.
- [ ] **Step 4: Run `python -m unittest tests.test_feedback_idempotency tests.test_feedback_loop -v` and verify GREEN.**
- [ ] **Step 5: Commit** with message `fix: preserve proven last-good strategy during learning`.

### Task 3: Hardened runner, Telegram alert, and production schedule

**Files:**
- Modify: `hardening_runner.py`
- Modify: `.github/workflows/facebook-autobot.yml`
- Modify: `tests/test_hardening_runner.py`
- Create: `tests/test_strategy_guard_wiring.py`

**Interfaces:**
- Add action `strategy_guard`.
- `resolve_jobs()` routes it through `strategy_guard.run_strategy_guard(db.execute)`.
- Outcome mapping: normal no-op states -> `skipped` or `success` per spec; rollback -> `success` and best-effort `notifications.send_message` alert; unknown/failure states -> exception.
- Production cron: `32 0 * * *`, after `learn` (`27 0 * * *`) and before Sunday `style_evolve` (`37 0 * * 0`) / daily planner (`47 0 * * *`).

- [ ] **Step 1: Write failing wiring tests** for action registration, job routing, outcome mapping, rollback alert, and exact workflow cron/order.
- [ ] **Step 2: Run focused wiring tests and verify RED.**
- [ ] **Step 3: Implement hardened adapter** without changing operational content jobs.
- [ ] **Step 4: Add manual workflow option, daily cron, and schedule mapping** for `strategy_guard`.
- [ ] **Step 5: Run focused wiring/hardening/workflow tests and verify GREEN.**
- [ ] **Step 6: Commit** with message `feat: schedule automatic strategy guard`.

### Task 4: Verification and pull request

**Files:** no production changes expected.

- [ ] **Step 1: Run full unit suite:** `python -m unittest discover -s tests -v`.
- [ ] **Step 2: Run compile gate:** `python -m compileall -q .`.
- [ ] **Step 3: Review branch diff against `main`** and confirm it is limited to Phase 4M scope.
- [ ] **Step 4: Open PR against `main`** summarizing thresholds, last-good semantics, schedule order, and verification evidence.
- [ ] **Step 5: Require green PR CI before presenting merge decision.**
