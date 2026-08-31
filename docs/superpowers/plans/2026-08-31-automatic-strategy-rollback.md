# Automatic Strategy Rollback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for completed work.

**Goal:** Detect >20% regression in mature adaptive Facebook performance, preserve a proven last-good strategy, and restore its weights before daily planning.

**Architecture:** Add a focused `strategy_guard` module that loads two adjacent 7-day mature cohorts, applies evidence/coverage gates, promotes healthy current strategy versions, or restores a last-good snapshot by creating a new rollback version. Modify feedback refresh so it no longer marks every daily version last-good, then wire the guard into the hardened runner and production workflow between learning and planning. Rollback persistence uses one Turso transaction so strategy rows, snapshot, and config move atomically.

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
- Rollback state mutation is atomic: partial strategy restoration is not allowed.

---

### Task 1: Strategy guard evidence and decision engine

**Files:**
- Create: `app/strategy_guard.py`
- Create: `tests/test_strategy_guard.py`
- Create: `tests/test_db_transaction.py`
- Modify: `app/db.py`

- [x] **Step 1:** Write RED evidence tests for mature-window boundaries, adaptive-only filtering, minimum sample gate, and 80% coverage gate.
- [x] **Step 2:** Verify RED with only new strategy-guard failures.
- [x] **Step 3:** Implement adjacent 7-day cohort loading delayed by the 72-hour final-metric horizon.
- [x] **Step 4:** Add RED decision tests for exact 20%, >20%, disabled adaptive, missing last-good, and healthy promotion.
- [x] **Step 5:** Implement deterministic decision logic.
- [x] **Step 6:** Add rollback persistence tests for restored/zeroed/safety weights and append-only versioning.
- [x] **Step 7:** Implement rollback persistence.
- [x] **Step 8:** During review, add RED transaction tests proving one connection/commit and whole-transaction rollback on statement failure.
- [x] **Step 9:** Add `db.execute_transaction()` and move all rollback writes into one retry-safe transaction callback.
- [x] **Step 10:** Verify GREEN for strategy-guard and transaction behavior.

### Task 2: Preserve last-good across daily learning refresh

**Files:**
- Modify: `app/feedback_loop.py`
- Modify: `tests/test_feedback_idempotency.py`
- Modify: `tests/test_feedback_loop.py`

- [x] **Step 1:** Change tests first so same-day repair updates only `current_strategy_version` and preserves the proven last-good pointer.
- [x] **Step 2:** Verify RED against the old behavior that marked each latest strategy last-good.
- [x] **Step 3:** Preserve `last_good_strategy_version` during same-day repair and new daily version creation; mark new snapshots `is_last_good=False`.
- [x] **Step 4:** Verify focused feedback tests GREEN.

### Task 3: Hardened runner, Telegram alert, and production schedule

**Files:**
- Modify: `hardening_runner.py`
- Modify: `.github/workflows/facebook-autobot.yml`
- Modify: `tests/test_hardening_runner.py`
- Create: `tests/test_strategy_guard_wiring.py`

- [x] **Step 1:** Write RED wiring tests for action registration, routing, outcome mapping, rollback alert, exact cron/order, and atomic transaction injection.
- [x] **Step 2:** Verify RED without unrelated regressions.
- [x] **Step 3:** Implement hardened outcome adapter and best-effort rollback Telegram alert.
- [x] **Step 4:** Route production rollback through `db.execute_transaction`.
- [x] **Step 5:** Add manual workflow option, daily `32 0 * * *` cron, and schedule mapping.
- [x] **Step 6:** Verify wiring/hardening/workflow tests GREEN.

### Task 4: Verification and pull request

- [x] **Step 1:** Run full unit suite through PR CI.
- [x] **Step 2:** Run Python compile gate through PR CI.
- [ ] **Step 3:** Review final branch diff against `main` and confirm it is limited to Phase 4M scope.
- [x] **Step 4:** Open PR #21 against `main` with Phase 4M scope.
- [ ] **Step 5:** Require green CI on the final documentation HEAD before presenting the merge decision.

## Verification evidence so far

- Initial strategy-guard RED: CI #290 — old suite passed; 8 new missing-module errors only.
- Strategy-guard GREEN: CI #291.
- Last-good semantics RED: CI #293 — 3 intended failures only.
- Last-good semantics GREEN: CI #294.
- Hardened wiring RED: CI #296 — intended wiring failures only.
- Initial wiring GREEN after contract fix: CI #299.
- Atomic rollback RED: CI #301 — exactly 3 new missing-transaction errors; all other tests passed.
- Atomic rollback GREEN: CI #304 — **309 tests passed** and Python compile gate passed.
