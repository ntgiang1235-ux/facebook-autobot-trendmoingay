# Phase 4C — Facebook Metrics and Content Score Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect 24h/72h Facebook post metrics with capability-aware fallbacks and calculate a normalized `content_score` that can safely feed adaptive learning.

**Architecture:** Add a Facebook metrics client that only consumes fields actually returned by the current Page/token, persist immutable snapshots, and score posts through deterministic normalization and outlier protection. Keep collection independent from publish jobs so API degradation never blocks posting.

**Tech Stack:** Python 3, `statistics`, JSON, Turso/libSQL, existing secure `requests.Session`, Facebook Graph API v25.0, `unittest`.

**Spec:** `docs/superpowers/specs/2026-08-29-adaptive-content-intelligence-design.md`

## Global Constraints

- Requires 4A merged; 4B may merge before or in parallel, but 4C must use `content_posts.facebook_post_id`.
- Canonical snapshots are 24h `early_score` and 72h `final_score`.
- Missing optional metrics must not fail the analytics job and must never be fabricated as zero.
- Preferred composite: 35% engagement, 30% reach/impressions, 20% comments/shares, 15% follower contribution.
- Fallback without follower contribution: 40% engagement, 35% reach/impressions, 25% comments/shares.
- If exposure metrics are unavailable, use a deterministic engagement-only fallback.
- Normalize relative to the Page's recent baseline and cap/winsorize outliers.

---

## File Map

- Create `app/facebook_metrics.py`: Graph API capability-aware collection.
- Create `app/scoring.py`: normalization, weighted interaction rate, fallbacks, outlier cap.
- Create `app/metrics_repository.py`: snapshot persistence/query.
- Modify `app/db.py`: add `content_metrics` table/indexes.
- Create `metrics_runner.py`: explicit hardened analytics entry point, no publishing.
- Modify `hardening_runner.py`: add `metrics` action only after unit coverage.
- Create `tests/test_facebook_metrics.py`, `tests/test_scoring.py`, `tests/test_metrics_repository.py`.
- Modify `tests/test_hardening_runner.py`.

### Task 1: Add metrics schema and repository

**Files:**
- Modify: `app/db.py`
- Create: `app/metrics_repository.py`
- Create: `tests/test_metrics_repository.py`
- Modify: `tests/test_db.py`

**Interfaces:**
- Produces `MetricSnapshot` and repository functions `save_snapshot`, `due_posts`, `recent_final_scores`.

- [ ] **Step 1: Write failing tests**

Require table columns: `id`, `facebook_post_id`, `measured_at`, `age_hours`, `reactions`, `comments`, `shares`, nullable `reach`, nullable `impressions`, nullable `video_views`, nullable `follower_delta`, nullable `engagement_rate`, `content_score`, `metric_capabilities`, `score_kind`. Require unique `(facebook_post_id, score_kind)` so retries upsert rather than duplicate canonical snapshots.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_metrics_repository tests.test_db -v`

Expected: FAIL for missing schema/module.

- [ ] **Step 3: Implement additive schema and repository**

Use parameterized SQL and `ON CONFLICT(facebook_post_id, score_kind) DO UPDATE`. `score_kind` must accept only `early` or `final`. `due_posts(execute_fn, now_iso)` returns published content whose 24h/72h snapshot is missing and whose age has reached the threshold.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_metrics_repository tests.test_db -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/db.py app/metrics_repository.py tests/test_db.py tests/test_metrics_repository.py
git commit -m "feat: add content metrics storage"
```

### Task 2: Implement capability-aware Facebook metrics client

**Files:**
- Create: `app/facebook_metrics.py`
- Test: `tests/test_facebook_metrics.py`

**Interfaces:**
- Produces `CollectedMetrics`, `collect_post_metrics(http, post_id, access_token) -> CollectedMetrics`.

- [ ] **Step 1: Write failing client tests**

Use a fake secure session. Cover: reaction/comment/share fields present; reach/impressions present; reach denied/missing; response with only engagement counts; Graph HTTP error raises `FacebookMetricsError`; optional field absence does not raise.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_facebook_metrics -v`

Expected: import failure.

- [ ] **Step 3: Implement collection**

Request the post fields/counts first and query insights separately so one denied insights metric does not erase basic engagement data. Return a `capabilities: frozenset[str]` that records only observed/valid metrics. Never coerce a missing field into numeric zero unless Graph explicitly returned zero.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_facebook_metrics -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/facebook_metrics.py tests/test_facebook_metrics.py
git commit -m "feat: collect capability-aware Facebook metrics"
```

### Task 3: Implement deterministic normalized scoring

**Files:**
- Create: `app/scoring.py`
- Test: `tests/test_scoring.py`

**Interfaces:**
- Produces:
  - `weighted_interactions(reactions, comments, shares) -> float`
  - `engagement_rate(metrics) -> float | None`
  - `winsorize(value, baseline_values, lower=0.05, upper=0.95) -> float`
  - `score_content(metrics, baseline, follower_available) -> ScoreResult`.

- [ ] **Step 1: Write failing scoring tests**

Assert `reactions + 2*comments + 3*shares`; denominator preference is reach when valid, otherwise impressions; zero/missing denominator returns `None`; a 100x viral value is capped to the recent distribution; full metric scoring uses 35/30/20/15; no-follower uses 40/35/25; no exposure metric uses a documented engagement-only mix of 60% weighted interactions relative performance + 40% comment/share relative performance.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_scoring -v`

Expected: import failure.

- [ ] **Step 3: Implement scoring**

Normalize each component into 0–100 against recent Page medians/percentile bands. When fewer than 5 baseline samples exist, return `maturity="insufficient_baseline"` and a conservative neutral component value of 50 rather than overfitting. Outlier capping must happen before relative normalization.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_scoring -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/scoring.py tests/test_scoring.py
git commit -m "feat: calculate normalized content scores"
```

### Task 4: Build the metrics collection job

**Files:**
- Create: `metrics_runner.py`
- Modify: `hardening_runner.py`
- Modify: `tests/test_hardening_runner.py`
- Create: `tests/test_metrics_runner.py`

**Interfaces:**
- Produces `collect_due_metrics(now=None) -> dict[str, int]` and hardened action `metrics`.

- [ ] **Step 1: Write failing orchestration tests**

Test one 24h due post, one 72h due post, one not-yet-due post, one post whose insights are partially unavailable, and one Graph hard failure. Hard failure for one post must be recorded/reported but collection should continue for remaining due posts; the overall action fails only if no due post can be processed because the upstream API is systemically unavailable.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_metrics_runner tests.test_hardening_runner -v`

Expected: FAIL because action/module is missing.

- [ ] **Step 3: Implement job wiring**

`metrics_runner.py` calls `db.ensure_schema()`, `due_posts()`, `collect_post_metrics()`, `score_content()`, and `save_snapshot()`. Add `metrics` to `VALID_ACTIONS` and `resolve_jobs()` without changing existing publish actions.

- [ ] **Step 4: Full verification**

Run:

```bash
python -m unittest discover -s tests -v
python -m compileall -q .
git diff --check
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add metrics_runner.py hardening_runner.py tests/test_metrics_runner.py tests/test_hardening_runner.py
git commit -m "feat: add hardened Facebook metrics job"
```

## 4C Acceptance Gate

Evidence must show 24h/72h idempotent snapshots, graceful capability fallback, no fabricated zeros, deterministic scoring, viral outlier protection, and no impact on publishing when metrics collection fails.
