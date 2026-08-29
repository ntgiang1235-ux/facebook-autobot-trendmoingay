# Phase 4 — Adaptive Content Intelligence Design

Date: 2026-08-29
Status: Approved design, pending implementation plan
Branch: `codex/phase-4-adaptive-content-spec`

## 1. Purpose

Upgrade TREND MỖI NGÀY from a schedule-driven publishing bot into a lightweight adaptive content system that can:

- increase content quality before publishing;
- prevent repeated topics and repeated writing patterns;
- measure post effectiveness using Facebook metrics that are actually available to the current Page/token;
- learn from the most recent 14 days, with stronger weight on the newest 3–7 days;
- automatically adjust category mix, posting time, hook, tone, CTA, format, and daily volume;
- keep daily volume within approximately ±20% of the baseline;
- allow a weak category to drop to zero normal slots, while periodically re-testing it;
- report daily and weekly learning decisions through Telegram;
- remain deployable on the existing GitHub Actions + Turso architecture without a VPS, Redis, vector database, background worker, or paid scheduler.

The system must remain deterministic at the decision layer. Gemini may generate and assess content, but it must not directly mutate schedules, strategy weights, schema, or infrastructure.

## 2. Approved Product Decisions

The following decisions are fixed for this phase:

- Primary optimization target: composite `content_score`, not a single metric.
- Learning window: 14 days.
- Recency weighting:
  - age 0–3 days: ×1.50;
  - age 4–7 days: ×1.25;
  - age 8–14 days: ×1.00;
  - older than 14 days: retained for reporting but excluded from current strategy decisions.
- Adaptive volume: may increase or decrease approximately ±20% from the baseline.
- Categories may be reduced to zero normal slots when performance is persistently weak and sample size is sufficient.
- Suspended categories are not permanently deleted; they receive a periodic re-test, initially once every 7 days.
- Writing strategy is adaptive: topic, hook, tone, CTA, length, format, and time slot may all learn from performance.
- Exploration policy: approximately 80% exploit / 20% explore.
- New styles may be generated from winning styles, but must pass the quality gate before publishing.
- Reporting: concise daily Telegram brief plus a deeper weekly intelligence report.
- Dynamic scheduling: GitHub Actions wakes a dispatcher every 30 minutes; Turso is the source of truth for the actual daily plan.

## 3. Architecture

```text
GitHub Actions (every 30 minutes)
        |
        v
Adaptive Dispatcher
        |
        +--> read daily_plan from Turso
        +--> stale / duplicate / cooldown / atomic-claim checks
        |
        v
Strategy Engine
        |
        +--> category
        +--> topic
        +--> hook
        +--> style/tone
        +--> CTA
        +--> format
        +--> time-slot strategy
        +--> exploit/explore/retest mode
        |
        v
Content Generator (Gemini)
        |
        v
Quality Gate
        |
        +--> exact duplicate
        +--> lexical similarity
        +--> semantic duplicate
        +--> novelty / readability / usefulness / tone / CTA
        +--> clickbait and repetition penalties
        |
        +--> fail: rewrite up to 2 times or skip candidate
        |
        v
Publish to Facebook
        |
        v
content_posts
        |
        +--> Metrics Collector (24h / 72h)
                    |
                    v
              content_metrics
                    |
                    v
               Scoring Engine
                    |
                    v
              strategy_stats
                    |
                    v
                Daily Planner
```

The existing hardened runner, secure HTTP policy, Turso retry layer, Telegram notification layer, job ledger, and stale-run protections remain foundational and must not be bypassed.

## 4. Data Model

### 4.1 `content_posts`

Stores one durable record per attempted/published content item.

Suggested fields:

- `id` — internal primary key;
- `run_key` — link to operational job run where applicable;
- `facebook_post_id` — nullable until publish succeeds;
- `action` / `category`;
- `topic_key` — normalized semantic topic identity;
- `topic_text` — human-readable topic;
- `source_url` — nullable;
- `source_title` — nullable;
- `content_text` or durable `content_hash` plus any text required for later duplicate detection;
- `hook_type`;
- `style_type` / `tone_type`;
- `cta_type`;
- `format_type` — text/photo/video/etc.;
- `scheduled_for`;
- `published_at`;
- `strategy_mode` — exploit/explore/retest;
- `quality_score`;
- `duplicate_score`;
- `strategy_version`;
- `status` — generated/rejected/published/failed/skipped;
- `detail` — concise reason for rejection/failure when needed.

### 4.2 `content_metrics`

Stores time-based measurement snapshots.

Suggested fields:

- `facebook_post_id`;
- `measured_at`;
- `age_hours` — expected canonical snapshots are 24h and 72h;
- `reactions`;
- `comments`;
- `shares`;
- `reach` — nullable;
- `impressions` — nullable;
- `video_views` — nullable;
- `follower_delta` — nullable;
- `engagement_rate` — nullable when denominator metrics are unavailable;
- `content_score`;
- `metric_capabilities` or equivalent metadata indicating which metrics were available.

### 4.3 `strategy_stats`

Stores derived 14-day adaptive state by dimension.

Dimensions include:

- category;
- topic/topic family;
- hook;
- style/tone;
- CTA;
- format;
- time slot.

Suggested fields:

- `dimension`;
- `value`;
- `sample_count`;
- `weighted_score_14d`;
- `recent_score_7d`;
- `success_rate`;
- `current_weight`;
- `last_used_at`;
- `status` — active/suspended/insufficient_data/retired;
- `cooldown_until` / `retest_after`.

### 4.4 `daily_plan`

Turso becomes the schedule source of truth.

Suggested fields:

- `id`;
- `plan_date`;
- `slot_time`;
- `category`;
- `strategy_mode` — exploit/explore/retest;
- `strategy_version`;
- `status` — planned/running/published/skipped/failed;
- `reason`;
- timestamps for plan creation, claim, completion.

### 4.5 Adaptive configuration and versioning

Store adaptive controls in a durable config/state table rather than hard-coding emergency behavior.

At minimum:

- `adaptive_enabled`;
- `auto_schedule_enabled`;
- `auto_suspend_enabled`;
- `exploration_rate` (default 0.20);
- current `strategy_version`;
- baseline daily volume;
- last known-good strategy version.

Strategy snapshots must be durable enough to roll back strategy state without rolling back application code.

## 5. Anti-Duplicate Design

Duplicate prevention is layered and cheapest checks run first.

### Layer 1 — Exact duplicate

Reject immediately when a known source URL/content identity has already been used inside the applicable window. Existing exact-link protection remains for news.

### Layer 2 — Lexical similarity

Normalize title/topic text and compare token/character similarity against recent relevant records.

Initial design threshold: a similarity around `0.80` is treated as a likely duplicate and rejected without a Gemini call. Exact threshold may be tuned during implementation tests but must remain explicit and test-covered.

### Layer 3 — Semantic duplicate

Only candidates not conclusively handled by Layers 1–2 reach Gemini semantic review.

Gemini receives the candidate topic plus a bounded set of recent comparable topics and returns structured output such as:

```json
{
  "duplicate": true,
  "similarity": 0.91,
  "reason": "same underlying event/topic"
}
```

The system must not require a vector database for Phase 4.

Initial anti-repeat windows:

- News: strongest check over 3–7 days;
- Finance: 7–14 days;
- Fun: 14 days;
- Recipe: 30 days for the same dish/concept;
- Philosophy: 30 days for the same core idea;
- Video: 14–30 days depending on topic/media identity.

These windows are independent from the 14-day strategy-learning window.

## 6. Quality Gate

Every generated draft must be evaluated before publishing.

Base quality score (0–100):

- novelty: 25%;
- hook quality: 20%;
- usefulness/information value: 20%;
- readability: 15%;
- page tone consistency: 10%;
- CTA/engagement potential: 10%.

Typical penalties:

- semantic duplicate: −40;
- hook too similar to recent posts: −20;
- excessive clickbait: −20;
- repetitive CTA: −10;
- format length outside the intended range: −10.

Decision thresholds:

- `>= 75`: publish candidate;
- `65–74`: rewrite;
- `< 65`: reject candidate and select another topic/candidate.

Maximum automatic rewrite attempts: 2.

Quality overrides volume. If a planned slot cannot obtain an acceptable candidate, the slot is recorded as `skipped_low_quality`; the bot must not publish weak content merely to meet a daily count.

## 7. Metrics and `content_score`

Metrics collection must be capability-aware because Meta fields and permissions can differ by token/Page/API version.

The collector probes or otherwise detects accessible metrics. Missing optional metrics must not fail the whole analytics job.

Canonical snapshots:

- 24h: `early_score`;
- 72h: `final_score`.

Strategy learning prefers `final_score`. An early score may be used at reduced influence when final data is not yet mature.

Engagement can be represented by a weighted interaction rate when a valid reach/impression denominator is available, for example:

```text
(reactions + 2*comments + 3*shares) / denominator
```

The denominator should use the most appropriate available Page/Post exposure metric and must be guarded against zero/missing values.

Preferred composite score when all dimensions are available:

- 35% engagement performance;
- 30% reach/impression performance;
- 20% comment/share quality/performance;
- 15% follower-growth contribution.

Fallback when follower contribution is unavailable:

- 40% engagement;
- 35% reach/impression performance;
- 25% comments/shares.

If reach/impressions are also unavailable, the implementation plan must define a deterministic engagement-only fallback rather than making up unavailable values.

Scores should be normalized relative to the Page's own recent baseline, not against a universal absolute benchmark. Outliers must be capped/winsorized so one viral post cannot dominate adaptive weights.

## 8. Adaptive Learning Engine

The decision engine is deterministic Python. Gemini does not directly choose final system weights.

The learner maintains performance by dimension:

- category;
- topic;
- hook;
- style/tone;
- CTA;
- format;
- time slot.

Learning uses the approved recency weights:

- 0–3 days: ×1.50;
- 4–7 days: ×1.25;
- 8–14 days: ×1.00.

Older data is retained for reporting only.

### Explore / exploit

Default behavior:

- ~80% exploit: favor proven strategies;
- ~20% explore: test new or under-sampled variants.

Selection uses weighted probability rather than pure winner-takes-all. Strong strategies receive higher probability but cannot monopolize all content.

New styles/hooks may be produced by Gemini as controlled variants of winning strategies. A new variant must pass the normal quality gate and begins in exploration state. After sufficient samples it can be promoted, retained as exploratory, or retired.

### Minimum samples

Strong adaptive actions require sufficient evidence.

Initial minimum sample target:

- category: at least 5 mature posts;
- hook/style/time slot: at least 5 mature posts.

Before that threshold the dimension is `insufficient_data` and must not be aggressively promoted or suppressed.

### Category suspension and re-test

A category with persistently weak weighted performance and sufficient sample size may be suspended to zero normal slots.

Suspension rules:

- no normal slots while suspended;
- initial re-test interval: 7 days;
- one controlled re-test slot;
- strong recovery can restore the category at a low weight;
- weak re-test returns it to suspension.

## 9. Dynamic Daily Planner

The planner creates the next day's `daily_plan` from mature metrics and current strategy state.

It decides:

- total planned publishing volume;
- category allocation;
- posting slots;
- exploit/explore/retest mode;
- category suspension effects;
- strategy version attached to every slot.

Daily volume is bounded to approximately ±20% of baseline. The exact integer min/max is derived deterministically from the configured baseline.

A low-quality day may finish below the nominal minimum because quality gating has higher priority than filling slots.

Weight changes must also be rate-limited. Initial guardrail: no individual strategy weight may move by more than approximately ±20% relative per day unless a separate emergency safety rule applies.

## 10. 30-Minute Dispatcher

GitHub Actions wakes the dispatcher every 30 minutes. GitHub's cron is not the business schedule; `daily_plan` is.

Dispatcher responsibilities:

1. load due planned slots;
2. enforce schedule freshness/stale policy;
3. atomically claim one eligible slot;
4. execute generation, quality checks, publication, and recording through the hardened production path;
5. finalize the slot status and reason.

Atomic claiming must ensure retries or concurrent workflow runs cannot publish the same planned slot twice. The implementation should use a conditional update/transaction pattern equivalent to:

```sql
UPDATE daily_plan
SET status = 'running'
WHERE id = ? AND status = 'planned';
```

and continue only when the claim actually succeeds.

Existing stale-run protection remains. A slot delayed beyond the approved freshness threshold is recorded as stale/skipped and does not contribute misleading content-quality learning data.

## 11. Telegram Intelligence Reports

### Daily brief

Daily output is intentionally concise and includes:

- published count;
- quality skips;
- current average/available score snapshot;
- strongest and weakest recent content;
- strategy changes made for the next plan;
- suspensions/retests;
- new experiments promoted or rejected;
- metric capability warnings when applicable.

The report must explicitly describe what the bot changed, so learning remains auditable rather than a black box.

### Weekly report

Weekly report includes:

- 7-day result;
- 14-day learning state;
- comparison with previous week where enough data exists;
- best/worst categories;
- best topics;
- best hooks/styles/CTA/formats;
- best posting windows;
- exploration results;
- suspended/reactivated categories;
- overall `content_score` trend;
- major strategy weight movements.

## 12. Safety, Rollback, and Failure Handling

### Kill switches

At minimum:

- `adaptive_enabled=false` returns to baseline/static strategy behavior;
- `auto_schedule_enabled=false` disables automatic dynamic schedule selection while preserving analytics;
- `auto_suspend_enabled=false` prevents categories from being automatically suspended.

These switches must not require a code rollback.

### Strategy versioning

Every adaptive strategy change creates or identifies a durable `strategy_version`. Every published post records the version that selected it.

The system must preserve a last known-good strategy snapshot for rollback.

### Automatic strategy rollback

Initial design trigger:

- recent 7-day composite performance regresses by more than 20%;
- minimum mature sample requirement is met;
- collector/metric health is not degraded;
- regression is attributable to adaptive strategy rather than missing data.

Then the strategy state rolls back to the last known-good version and Telegram sends an alert.

This is a strategy rollback only, not a code rollback.

### Metric/API degradation

When Facebook removes or denies a metric:

- collector records the reduced capability;
- score uses the documented fallback;
- analytics continues where possible;
- Telegram warns when the degradation materially affects interpretation;
- no fabricated zero values are used as if they were real measurements.

Observability failures must not mask the original business failure, preserving the existing hardening contract.

## 13. GitHub Free / Operational Footprint

The target production topology remains lightweight:

- GitHub Actions for dispatch, metrics collection, daily reporting, weekly reporting, and CI;
- Turso for state and history;
- Facebook Graph API for publishing and metrics;
- Gemini for generation/semantic assessment;
- Telegram for operational and intelligence reporting.

No Phase 4 requirement depends on:

- VPS;
- always-on process;
- Docker in production;
- Redis;
- vector database;
- queue service;
- paid scheduler.

Expected workflow families after migration:

- adaptive dispatcher — every 30 minutes;
- metrics collector — periodic;
- daily intelligence — once daily;
- weekly intelligence — once weekly;
- CI — PR/push only.

Implementation should avoid unnecessary API/model calls by running exact and lexical duplicate checks before semantic Gemini checks, bounding recent-history context, and exiting dispatcher runs quickly when no slot is due.

## 14. Phase Decomposition

Implementation is intentionally split into small reviewable sub-phases rather than one large PR:

### 4A — Content Ledger + Semantic Anti-Duplicate

- durable content history;
- normalized topic identity;
- exact + lexical + semantic duplicate checks;
- action-specific anti-repeat windows.

### 4B — Quality Gate + Style/Hook Registry

- structured content metadata;
- quality scoring and penalties;
- rewrite/reject behavior;
- controlled style/hook/CTA experiments.

### 4C — Facebook Metrics + Content Score

- capability-aware metrics collection;
- 24h/72h snapshots;
- page-relative normalization;
- composite score and deterministic fallbacks.

### 4D — Adaptive Strategy Engine

- 14-day recency-weighted statistics;
- 80/20 explore/exploit;
- minimum-sample rules;
- rate-limited weight changes;
- suspend/retest/promote/retire lifecycle;
- strategy version snapshots.

### 4E — Dynamic Daily Planner + Dispatcher

- ±20% daily volume bounds;
- daily plan generation;
- 30-minute wake-up workflow;
- atomic slot claiming;
- stale/no-duplicate execution behavior.

### 4F — Daily/Weekly Intelligence Reporting

- concise daily Telegram brief;
- weekly content intelligence report;
- explicit strategy-change audit trail.

### 4G — Safety + Production Learning Verification

- kill switches;
- regression detection;
- automatic strategy rollback;
- degraded-metric verification;
- production observation proving that learning, scheduling, and reporting behave as designed.

## 15. Testing Requirements

Implementation must use TDD and preserve the existing branch/PR/required-CI flow.

Tests must cover at least:

- DB migration/idempotent schema creation;
- exact, lexical, and semantic duplicate decisions;
- per-category anti-repeat windows;
- quality thresholds and maximum rewrite count;
- metric capability fallbacks;
- 24h/72h maturity rules;
- recency-weight calculations;
- outlier protection;
- minimum-sample behavior;
- weighted selection and fixed exploration floor;
- category suspension and 7-day re-test;
- ±20% planner volume bounds;
- maximum daily strategy-weight movement;
- atomic slot claiming / duplicate dispatch prevention;
- stale-slot handling;
- kill switches;
- strategy version rollback;
- daily/weekly report summaries;
- observability failures not masking original failures.

Live Graph API verification must distinguish between code correctness and the actual metrics/permissions available to the production Page token. CI must not require production secrets.

## 16. Success Criteria

Phase 4 is complete only when all of the following are true:

1. Every production post is represented in the content ledger with strategy metadata.
2. Same-event/topic repetition can be blocked even when the source URL or wording differs.
3. Low-quality drafts are rewritten/rejected before Facebook publish.
4. The system collects usable 24h/72h performance data using graceful metric fallbacks.
5. A deterministic composite score can rank posts relative to the Page's recent baseline.
6. The strategy engine uses 14-day data with the approved recency weights.
7. Topic/category/style/hook/time/format choices adapt while maintaining approximately 80/20 exploration.
8. A weak category can be suspended to zero normal slots only after sufficient evidence and can later re-enter through re-test.
9. Daily publishing volume is adaptively planned within approximately ±20% baseline, except intentional quality/stale skips.
10. The dispatcher can run every 30 minutes without double-publishing a slot.
11. Daily and weekly Telegram reports explain both performance and strategy changes.
12. Adaptive behavior can be disabled or rolled back without reverting application code.
13. CI remains secret-independent and green through the protected-branch PR workflow.
14. Production verification demonstrates at least one complete plan → publish → metrics → score → strategy-update cycle before Phase 4 is declared fully complete.

## 17. Non-Goals

Phase 4 does not include:

- training a custom ML model;
- adding a vector database;
- adding a VPS or persistent worker;
- building a web analytics dashboard;
- allowing Gemini to directly modify infrastructure, schedules, schemas, or strategy weights;
- automatically rewriting application code or GitHub workflows as a learning action;
- maximizing posting volume at the expense of quality.

These may be considered later only if the lightweight design proves insufficient.
