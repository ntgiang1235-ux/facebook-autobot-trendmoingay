# Phase 4N — Production Readiness & End-to-End Verification

Date: 2026-08-31
Status: Approved; implementation pending final verification
Branch: `codex/phase-4n-production-readiness`
PR: #22

## 1. Purpose

Phase 4 already provides generation, pre-publish safety, a canonical publication ledger, Facebook metrics, 14-day adaptive learning, dynamic daily planning, controlled style experiments, strategy versioning, kill switches, and automatic regression rollback.

Phase 4N closes the remaining operational gap with a repeatable production verification step that answers:

> Is the deployed adaptive state internally consistent and capable of producing a safe next-day plan without publishing anything or mutating production state?

Phase 4N does not add a new content feature and does not change the adaptive strategy.

## 2. Non-negotiable safety properties

The readiness path is strictly read-only with respect to Turso and all external publishing systems.

It MUST NOT:

- call `db.ensure_schema()`;
- call `db.record_job()`;
- execute `INSERT`, `UPDATE`, `DELETE`, `ALTER`, `CREATE`, `DROP`, `REPLACE`, or any other mutating SQL;
- use generic `WITH` statements in the read adapter;
- write `daily_plan`;
- create or modify strategy versions;
- modify `adaptive_config`, `strategy_stats`, or `style_registry`;
- call Facebook publishing endpoints;
- call Gemini;
- call Pexels;
- send Telegram messages;
- run automatically on a cron schedule.

The gate is initiated manually through `workflow_dispatch`.

## 3. Architecture

Use a standalone verification path rather than adding an action to `hardening_runner.py`.

Reason: `hardening_runner.py` intentionally performs operational bookkeeping and schema maintenance. Reusing it would violate the read-only contract even if the verifier itself issued only reads.

The subsystem consists of:

- `app/readiness.py` — deterministic validation logic and result models;
- `app/readiness_db.py` — read-only Turso adapter with query enforcement and transient retry;
- `readiness_runner.py` — CLI that prints the result and returns a non-zero exit code on hard failure;
- `.github/workflows/production-readiness.yml` — manual-only workflow using only Turso secrets;
- readiness tests under `tests/`.

Shadow planning reuses `app.planner.build_daily_plan()` directly. No duplicate planner implementation is allowed.

## 4. Read-only database enforcement

`app/readiness_db.py` must enforce the safety contract before a query reaches Turso.

Allowed statements:

- `SELECT ...`
- exact `PRAGMA table_info(<identifier>)`

The adapter:

1. validates `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN`;
2. strips leading whitespace/comments for classification;
3. rejects stacked statements;
4. rejects every non-read statement before connecting/executing;
5. never calls `commit()`;
6. closes the connection in `finally`;
7. reuses the existing `run_with_retry` policy for transient upstream failures.

Tests must prove write statements, malformed PRAGMAs, CTE-prefixed statements, and stacked statements are rejected before execution.

## 5. Result model

Each check returns:

- `name`
- `status`: `ready`, `degraded`, or `failed`
- `detail`

Aggregate precedence is strict:

1. any `failed` => aggregate `failed`, exit code 1;
2. otherwise any `degraded` => aggregate `degraded`, exit code 0;
3. otherwise => aggregate `ready`, exit code 0.

`degraded` means structurally safe but insufficient adaptive evidence. It must never hide corruption or query/dependency failure.

## 6. Required schema

Readiness checks the existing production schema without creating or altering it.

Required tables:

- `job_runs`
- `content_posts`
- `content_metrics`
- `style_registry`
- `strategy_stats`
- `adaptive_config`
- `strategy_versions`
- `daily_plan`

At minimum, required runtime columns include:

- `content_posts`: Facebook ID, category, hook/style/CTA/format, experiment key, schedule/publish metadata, strategy mode/version, status;
- `content_metrics`: Facebook ID, score kind, content score;
- `style_registry`: dimension, value, parent, status, lifecycle timestamps;
- `strategy_stats`: dimension/value, sample count, 14d/7d scores, success rate, weight, status, retest metadata;
- `adaptive_config`: all kill switches, exploration rate, baseline volume, current pointer, last-good pointer;
- `strategy_versions`: version ID, weights/config JSON, creation/reason metadata, historical `is_last_good` value;
- `daily_plan`: plan/slot identity, planned time, action/category, strategy metadata, status and claim metadata.

Missing required table or column is `failed`.

## 7. Adaptive config validation

Readiness reads the raw `adaptive_config` row rather than using runtime defaults so it can distinguish real production state from a silent fallback.

Rules:

- exactly one row with `id = 1` must exist;
- boolean controls must be stored as 0/1-compatible values;
- `exploration_rate` must be finite and in `[0.0, 1.0]`;
- `baseline_daily_volume` must be a positive integer;
- current and last-good version pointers, when non-null, must be positive integers.

Missing/invalid config is `failed`.

## 8. Strategy version and last-good invariants

Phase 4M's append-only contract remains authoritative:

- `strategy_versions` is append-only;
- `adaptive_config.last_good_strategy_version` is the canonical last-good pointer;
- historical `strategy_versions.is_last_good` values are immutable metadata and are not rewritten when the canonical pointer advances;
- Phase 4N never attempts to synchronize or repair historical flags.

Readiness rules:

- current pointer, when non-null, must reference an existing snapshot;
- last-good pointer, when non-null, must reference an existing snapshot;
- last-good cannot be numerically newer than current when both exist;
- `weights_json` and `config_json` must parse as JSON objects;
- the current snapshot's `current_strategy_version` must identify its own version;
- stored `is_last_good` values must be valid 0/1 data, but they do NOT override or need to match the canonical pointer.

Classification:

- dangling pointer, malformed snapshot JSON, invalid historical flag storage, or self-inconsistent current snapshot => `failed`;
- adaptive enabled but no current version => `degraded`;
- current version exists but no canonical last-good target => `degraded`;
- historical flag/pointer differences caused by append-only history are valid and do not degrade readiness.

## 9. Strategy stats invariants

Canonical dimensions are exactly those produced by the feedback loop:

- `category`
- `time_bucket`
- `format_type`
- `hook_type`
- `style_type`
- `cta_type`

For every row:

- numeric fields must be finite;
- `sample_count >= 0`;
- `current_weight >= 0`;
- `success_rate` in `[0, 1]`;
- 14d/7d scores in `[0, 100]`;
- `suspended` and `retired` rows have zero effective weight;
- category values belong to planner `CORE_ACTIONS`;
- non-suspended/non-retired time buckets belong to planner `SAFE_TIME_BUCKETS`;
- unknown strategy dimensions fail closed.

Absence of mature stats is not corruption; learning readiness handles it as `degraded`.

`format_type` is checked for integrity only. Making format adaptive remains a future feature.

## 10. Style registry invariants

Allowed registry dimensions:

- `hook`
- `tone`
- `cta`

Allowed statuses:

- `baseline`
- `explore`
- `active`
- `retired`

Rules:

- registry identities are unique;
- at most one `explore` experiment exists across all dimensions;
- each `explore` row has a non-empty parent;
- the parent exists in the same dimension and is not retired;
- baseline rows have no experiment parent;
- `explore` and `retired` registry values cannot have positive exploitable strategy weight.

No pending experiment is normal and `ready`.

## 11. Cross-table mapping

The existing production mapping is reused:

- registry `hook` -> strategy `hook_type`
- registry `tone` -> strategy `style_type`
- registry `cta` -> strategy `cta_type`

Contradictory lifecycle/weight state is `failed`. Missing stats for a new/unseen registry value are acceptable.

## 12. Learning readiness

The verifier uses planner maturity rules, not new thresholds.

- insufficient mature category learning => `degraded`;
- insufficient mature time-bucket learning => `degraded`;
- adaptive/auto-schedule disabled => baseline operation is structurally valid;
- sufficient category and time maturity => `ready`.

The gate does not rerun Facebook metrics collection or external health checks; existing `health` and `metrics` jobs own those responsibilities.

## 13. Shadow daily-plan verification

Readiness builds one in-memory plan for the next Vietnam calendar day using:

- validated `AdaptiveConfig`;
- current category/time strategy stats;
- existing `planner.build_daily_plan()`;
- a deterministic supplied/current `now` value.

The verifier never calls `plan_repository.save_slots()`.

Every emitted slot must satisfy:

- at least one slot is produced;
- action/category belong to `CORE_ACTIONS` and agree;
- slot IDs are unique;
- planned timestamps are unique and chronological;
- Vietnam-local date is the target next day;
- local time belongs to `SAFE_TIME_BUCKETS`;
- status is `planned`;
- claim/finish metadata is empty;
- mode is `baseline`, `exploit`, `explore`, or `retest`;
- adaptive slots carry the current strategy version.

Volume rules reuse planner guardrails:

- baseline fallback emits exactly `baseline_daily_volume` slots;
- adaptive output stays between `ceil(baseline*0.80)` and `floor(baseline*1.20)` with the planner's lower-bound handling.

Planner exception, duplicate/unsafe output, invalid metadata, or guardrail violation is `failed`.

If learning is insufficient but the planner safely falls back, the shadow-plan check is `ready` while the separate learning check is `degraded`.

## 14. CLI and workflow

`readiness_runner.py` prints a compact result such as:

```text
PHASE_4_READINESS: DEGRADED
[READY] schema — 8 required tables and runtime columns present
[READY] adaptive_config — id=1 baseline=12 explore=20%
[READY] strategy_versions — current=v8, last_good=v7
[READY] strategy_stats — 34 strategy rows have valid dimensions, states, and weights
[READY] style_registry — 17 registry rows valid; pending_explore=0
[DEGRADED] learning — insufficient mature learning for: time_bucket
[READY] shadow_plan — 12 unique safe slots for 2026-09-01: ...
```

Unexpected Turso/query errors are hard failures, not degradation.

`.github/workflows/production-readiness.yml` has:

- `workflow_dispatch` only;
- `contents: read` permission;
- Python 3.11;
- pinned repository-standard checkout/setup-python SHAs;
- pinned `requirements.txt` install;
- only `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` secrets;
- `python readiness_runner.py` as the verification command.

The existing production scheduling workflow is not modified by Phase 4N.

## 15. TDD and final verification

Implementation follows RED -> GREEN for:

1. read-only adapter;
2. schema/config/version/stat/style invariants;
3. learning + shadow planner + runner exit contract;
4. manual-only workflow wiring;
5. Phase 4M append-only last-good regression protection.

Before merge:

- full unittest suite must pass;
- `python -m compileall -q .` must pass;
- PR scope must contain only approved 4N/4N.0 files;
- PR must be mergeable;
- code review must find no unresolved correctness/safety issue.

After explicit merge approval:

1. merge PR #22;
2. verify post-merge CI on `main`;
3. manually run `Production Readiness` with production Turso secrets;
4. review the resulting `READY`, `DEGRADED`, or `FAILED` checks.

Phase 4 is closed only when the production run has no `FAILED` checks. `DEGRADED` may be accepted only when it reflects documented lack of maturity/evidence rather than corruption.

## 16. Out of scope

Phase 4N does not:

- make `format_type` adaptive;
- change scoring formulas or thresholds;
- change rollback thresholds;
- add content categories or posting schedules;
- call Facebook/Pexels/Gemini/Telegram for smoke testing;
- repair production state automatically;
- mutate state merely to make readiness pass;
- change branch-protection rules.

If readiness exposes existing corrupt state, remediation is a separate explicit, reviewed change. The verifier remains read-only.

## 17. Acceptance criteria

Phase 4N is ready for merge when:

- the standalone runner cannot issue write SQL through its adapter;
- schema/config/version/stat/style invariants are covered by tests;
- canonical last-good semantics preserve append-only `strategy_versions` history;
- shadow planning uses production planner code and never persists;
- the readiness workflow is manual-only and Turso-only;
- all unit tests and Python compilation pass on final PR HEAD;
- PR review has no unresolved important finding.

Phase 4 closure additionally requires a post-merge production readiness run with no `FAILED` checks.
