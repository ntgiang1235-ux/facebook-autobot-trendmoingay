# Phase 4D — Adaptive Strategy Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert mature 14-day content performance into safe adaptive weights for category, topic, hook, style, CTA, format, and time slot.

**Architecture:** Keep learning deterministic and testable. Aggregate mature `content_metrics` into dimension-level statistics, apply fixed recency weights, enforce minimum samples and daily movement caps, then use weighted random selection with an explicit 80/20 exploit/explore split. Store all strategy state/version snapshots in Turso.

**Tech Stack:** Python 3, stdlib `random`, `statistics`, dataclasses, Turso/libSQL, `unittest`.

**Spec:** `docs/superpowers/specs/2026-08-29-adaptive-content-intelligence-design.md`

## Global Constraints

- Requires 4A, 4B, and 4C merged.
- Learning window: 14 days.
- Recency weights: 0–3d ×1.50; 4–7d ×1.25; 8–14d ×1.00; >14d excluded from current strategy decisions.
- Mature/final 72h score has priority; early 24h score may only contribute at reduced influence.
- Minimum strong-action sample target: 5 mature posts for category/hook/style/time slot.
- Default exploration rate: 0.20.
- Individual strategy weight movement capped at approximately ±20% relative/day.
- Categories may suspend to zero normal slots only after enough evidence and must re-test after 7 days.
- Gemini cannot directly write strategy weights.

---

## File Map

- Create `app/strategy_models.py`: strategy dataclasses/enums.
- Create `app/strategy_repository.py`: strategy stats/config/version persistence.
- Create `app/learning.py`: recency aggregation, maturity, suspension, movement limits.
- Create `app/selection.py`: exploit/explore weighted selection.
- Modify `app/db.py`: `strategy_stats`, `adaptive_config`, `strategy_versions` tables.
- Create `tests/test_strategy_repository.py`, `tests/test_learning.py`, `tests/test_selection.py`.

### Task 1: Add strategy state schema and typed models

**Files:**
- Create: `app/strategy_models.py`
- Create: `app/strategy_repository.py`
- Modify: `app/db.py`
- Test: `tests/test_strategy_repository.py`
- Modify: `tests/test_db.py`

**Interfaces:**
- Produces `StrategyStat`, `AdaptiveConfig`, `StrategySnapshot`.
- Repository functions: `load_config`, `save_config`, `load_stats`, `upsert_stat`, `save_strategy_version`, `load_strategy_version`.

- [ ] **Step 1: Write failing tests**

Require config defaults: `adaptive_enabled=True`, `auto_schedule_enabled=True`, `auto_suspend_enabled=True`, `exploration_rate=0.20`, baseline daily volume configurable, current/last-good strategy version. Require unique `(dimension, value)` strategy stats and immutable strategy version snapshots.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_strategy_repository tests.test_db -v`

Expected: FAIL for missing schema/modules.

- [ ] **Step 3: Implement additive schema/models**

`strategy_stats` contains `dimension`, `value`, `sample_count`, `weighted_score_14d`, `recent_score_7d`, `success_rate`, `current_weight`, `last_used_at`, `status`, `cooldown_until`, `retest_after`, `updated_at`. `adaptive_config` is a single-row key/value or typed row design with deterministic defaults. `strategy_versions` stores version id, serialized normalized weights/config, created_at, reason, and `is_last_good`.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_strategy_repository tests.test_db -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/strategy_models.py app/strategy_repository.py app/db.py tests/test_strategy_repository.py tests/test_db.py
git commit -m "feat: add adaptive strategy state"
```

### Task 2: Implement recency-weighted aggregation

**Files:**
- Create: `app/learning.py`
- Test: `tests/test_learning.py`

**Interfaces:**
- Produces `recency_weight(age_days) -> float`, `aggregate_dimension(samples, now) -> LearningStat`.

- [ ] **Step 1: Write failing tests**

Assert exact weights at ages 0, 3, 4, 7, 8, 14 and zero beyond 14. Assert a 72h/final sample contributes full weight and a 24h/early sample contributes half the computed recency weight. Assert sample_count for strong decisions counts mature/final samples, not early-only snapshots.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_learning.LearningAggregationTests -v`

Expected: import failure.

- [ ] **Step 3: Implement aggregation**

Use weighted mean `sum(score * effective_weight) / sum(effective_weight)`. Track `weighted_score_14d`, `recent_score_7d`, `mature_sample_count`, and `success_rate` where success means score >= recent Page median/50th percentile baseline supplied to the function.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_learning.LearningAggregationTests -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/learning.py tests/test_learning.py
git commit -m "feat: add recency weighted learning"
```

### Task 3: Add safe weight updates and minimum-sample rules

**Files:**
- Modify: `app/learning.py`
- Modify: `tests/test_learning.py`

**Interfaces:**
- Produces `propose_weight(current_weight, score, peer_scores, mature_samples) -> WeightProposal`.

- [ ] **Step 1: Write failing tests**

Cases: 4 mature samples => `insufficient_data` and no aggressive weight change; 5 samples and strong score => increase but at most +20% relative; weak score => decrease but at most -20% relative; all peers equal => stable weight; weights normalize to sum 1 across active options after applying per-item caps.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_learning.WeightUpdateTests -v`

Expected: FAIL for missing proposal function.

- [ ] **Step 3: Implement deterministic proposals**

Map relative performance to a bounded target multiplier, then clamp to `[current*0.8, current*1.2]`. Apply a small positive floor to active non-suspended options so weighted probability cannot collapse to zero accidentally.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_learning.WeightUpdateTests -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/learning.py tests/test_learning.py
git commit -m "feat: cap adaptive weight movement"
```

### Task 4: Implement category suspension and re-test state machine

**Files:**
- Modify: `app/learning.py`
- Modify: `tests/test_learning.py`

**Interfaces:**
- Produces `evaluate_category_status(stat, peer_median, now, auto_suspend_enabled) -> CategoryDecision`.

- [ ] **Step 1: Write failing tests**

Assert fewer than 5 mature posts cannot suspend; one bad viral/outlier-normalized post cannot suspend; persistently weak category with >=5 mature samples may suspend; suspended category sets `retest_after=now+7 days`; before re-test date it remains excluded from normal allocation; at/after re-test date exactly one controlled retest is eligible; strong re-test restores at low weight; weak re-test renews suspension.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_learning.CategorySuspensionTests -v`

Expected: FAIL.

- [ ] **Step 3: Implement state machine**

Use explicit states `active`, `insufficient_data`, `suspended`. `auto_suspend_enabled=False` must prevent transitions into suspended while retaining performance measurements.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_learning.CategorySuspensionTests -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/learning.py tests/test_learning.py
git commit -m "feat: suspend and retest weak categories"
```

### Task 5: Implement 80/20 exploit/explore selection

**Files:**
- Create: `app/selection.py`
- Test: `tests/test_selection.py`

**Interfaces:**
- Produces `select_mode(rng, exploration_rate=0.20) -> str`, `weighted_choice(options, rng)`, `select_strategy(dimension_stats, exploratory_values, rng, exploration_rate) -> Selection`.

- [ ] **Step 1: Write failing deterministic RNG tests**

Use a seeded/fake RNG so tests do not assert statistical flukes. Assert explore when random value <0.20, exploit otherwise; exploit excludes suspended values; explore prefers under-sampled/registered exploratory variants; a high score gets higher probability but not probability 1.0; empty explore pool falls back to exploit safely.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_selection -v`

Expected: import failure.

- [ ] **Step 3: Implement selection**

Use cumulative weighted selection from explicit positive weights. Keep RNG injectable. Return `Selection(value, mode, reason)` so later reporting can explain the choice.

- [ ] **Step 4: Verify GREEN and full suite**

Run:

```bash
python -m unittest tests.test_selection tests.test_learning tests.test_strategy_repository -v
python -m unittest discover -s tests -v
python -m compileall -q .
git diff --check
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/selection.py tests/test_selection.py
git commit -m "feat: add explore exploit strategy selection"
```

## 4D Acceptance Gate

Evidence must show exact 14-day recency weights, final-score preference, minimum sample enforcement, ±20% weight movement cap, suspension/retest behavior, deterministic seeded selection, and no direct Gemini mutation of strategy state.
