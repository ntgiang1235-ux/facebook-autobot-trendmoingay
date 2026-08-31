# Phase 4N — Production Readiness & End-to-End Verification

Date: 2026-08-31
Status: Design approved in chat; implementation pending written-spec review
Branch: `codex/phase-4n-production-readiness`
PR: #22

## 1. Purpose

Phase 4 already has generation, pre-publish safety, canonical publication ledger, Facebook metrics, 14-day adaptive learning, dynamic daily planning, style experiments, strategy versioning, kill switches, and automatic regression rollback.

The remaining closure gap is a repeatable production verification step that can answer:

> Is the deployed adaptive state internally consistent and capable of producing a safe next-day plan without publishing anything or mutating production state?

Phase 4N adds that verification gate. It does not add a new content feature and does not change the adaptive strategy itself.

## 2. Non-negotiable safety properties

The readiness path MUST be read-only with respect to Turso and all external publishing systems.

It MUST NOT:

- call `db.ensure_schema()`;
- call `db.record_job()`;
- execute `INSERT`, `UPDATE`, `DELETE`, `ALTER`, `CREATE`, `DROP`, `REPLACE`, or other mutating SQL;
- write `daily_plan`;
- create or modify strategy versions;
- modify `adaptive_config`, `strategy_stats`, or `style_registry`;
- call Facebook publishing endpoints;
- call Gemini;
- call Pexels;
- send Telegram messages;
- run automatically on a cron schedule.

It MUST be initiated manually through `workflow_dispatch`.

## 3. Architecture decision

### Chosen approach: standalone read-only readiness runner

Create a separate verification path instead of adding another action to `hardening_runner.py`.

Reason: `hardening_runner.py` intentionally performs operational bookkeeping and schema maintenance. Reusing it would violate the read-only contract even if the verifier itself only used SELECT statements.

The readiness subsystem will contain:

- `app/readiness.py` — pure/deterministic validation logic and result models;
- `readiness_runner.py` — small CLI entry point that opens the production read-only query adapter, runs checks, prints a human-readable summary, and exits non-zero on hard failure;
- `app/readiness_db.py` — read-only Turso adapter with retry behavior and a statement allowlist;
- `.github/workflows/production-readiness.yml` — manual-only GitHub Actions workflow using production Turso secrets;
- unit/integration-style tests under `tests/`.

The existing `app/planner.build_daily_plan()` will be reused directly for shadow planning. No duplicate planner implementation is allowed.

## 4. Read-only database enforcement

The verifier must not rely only on developer discipline. The DB adapter will enforce the contract.

`app/readiness_db.py` will:

1. validate `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN`;
2. open a Turso/libSQL connection;
3. expose `execute_read(query, params=())`;
4. normalize leading whitespace/comments before validation;
5. allow only static read statements needed by readiness:
   - `SELECT ...`
   - `PRAGMA table_info(...)`
6. reject every other statement before sending it to Turso;
7. never call `commit()`;
8. close the connection in `finally`;
9. reuse the existing transient retry policy (`run_with_retry`) for upstream transport failures.

The verifier will not use generic `WITH` statements, because SQLite CTE syntax can prefix mutating statements and would weaken the allowlist.

Tests must prove a write such as `UPDATE adaptive_config ...` is rejected before the fake connection receives it.

## 5. Readiness result model

Each check returns a structured item:

- `name`
- `status`: `ready`, `degraded`, or `failed`
- `detail`

The aggregate result follows strict precedence:

1. any `failed` check => aggregate `failed` and process exit code 1;
2. otherwise any `degraded` check => aggregate `degraded` and process exit code 0;
3. otherwise aggregate `ready` and process exit code 0.

`degraded` means the system is structurally safe but has not accumulated enough adaptive state to exercise every learning capability. It must not be used to hide corruption.

## 6. Required schema checks

Readiness will query `sqlite_master` and `PRAGMA table_info` without creating anything.

The following runtime tables are required:

- `job_runs`
- `content_posts`
- `content_metrics`
- `style_registry`
- `strategy_stats`
- `adaptive_config`
- `strategy_versions`
- `daily_plan`

At minimum, the verifier checks columns that Phase 4 runtime depends on, including:

- `content_posts`: `facebook_post_id`, `category`, `hook_type`, `style_type`, `cta_type`, `format_type`, `style_experiment_key`, `scheduled_for`, `published_at`, `strategy_mode`, `strategy_version`, `status`;
- `content_metrics`: `facebook_post_id`, `score_kind`, `content_score`;
- `style_registry`: `dimension`, `value`, `parent_value`, `status`;
- `strategy_stats`: `dimension`, `value`, `sample_count`, `weighted_score_14d`, `recent_score_7d`, `success_rate`, `current_weight`, `status`, `retest_after`;
- `adaptive_config`: all existing kill switches, exploration rate, baseline volume, current strategy pointer, last-good pointer;
- `strategy_versions`: `version_id`, `weights_json`, `config_json`, `created_at`, `reason`, `is_last_good`;
- `daily_plan`: plan identity, planned time, action/category, strategy metadata, state, and claim metadata.

Missing required table/column is `failed`, not `degraded`.

## 7. Adaptive config validation

Unlike `strategy_repository.load_config()`, readiness MUST read the raw `adaptive_config` row so it can distinguish real production state from deterministic defaults.

Rules:

- exactly one row with `id = 1` must exist;
- boolean controls must be representable as 0/1;
- `exploration_rate` must be finite and within `[0.0, 1.0]`;
- `baseline_daily_volume` must be a positive integer;
- version pointers, when non-null, must be positive integers.

Missing config row or invalid typed values are `failed` because production would otherwise silently fall back to defaults in normal runtime.

## 8. Strategy version and last-good invariants

Readiness verifies pointer/snapshot integrity without changing it.

Rules:

- if `current_strategy_version` is non-null, the referenced snapshot must exist;
- if `last_good_strategy_version` is non-null, the referenced snapshot must exist;
- `last_good_strategy_version` cannot be numerically greater than `current_strategy_version` when both exist;
- when a last-good pointer exists, exactly one `strategy_versions.is_last_good = 1` row must exist and its `version_id` must equal the pointer;
- when no last-good pointer exists, there must be no `is_last_good = 1` row;
- snapshot `weights_json` and `config_json` must parse as JSON objects;
- current snapshot configuration must not point to a different current version than its own `version_id`.

Classification:

- dangling pointer, duplicate/wrong last-good audit flag, malformed snapshot JSON, or self-inconsistent current snapshot => `failed`;
- adaptive enabled but no current strategy version yet => `degraded` (safe baseline operation, learning not established);
- current strategy exists but no last-good version yet => `degraded` (strategy may operate, but automatic rollback has no proven target yet).

Rollback snapshots are allowed to have a newer current version while preserving an older last-good pointer.

## 9. Strategy stats invariants

Canonical dimensions are exactly the dimensions produced by the feedback loop:

- `category`
- `time_bucket`
- `format_type`
- `hook_type`
- `style_type`
- `cta_type`

For every strategy-stat row:

- numeric fields must be finite;
- `sample_count >= 0`;
- `current_weight >= 0`;
- `success_rate` must be within `[0.0, 1.0]`;
- `weighted_score_14d` and `recent_score_7d` must be within `[0.0, 100.0]`;
- `suspended` and `retired` rows must have zero effective weight;
- category values must belong to planner `CORE_ACTIONS`;
- time buckets participating in adaptive scheduling must belong to planner `SAFE_TIME_BUCKETS`.

An invalid number, negative weight, unknown category that could enter planning, nonzero suspended/retired weight, or unsafe active time bucket is `failed`.

Absence of mature stats is not corruption; it becomes `degraded` through shadow-plan/learning-readiness checks.

The `format_type` dimension is validated for integrity but Phase 4N does not start using it for selection. Adaptive format selection remains a separate future feature.

## 10. Style registry invariants

The verifier reuses the registry vocabulary already established by `app/style_registry.py`.

Rules:

- dimensions must be `hook`, `tone`, or `cta`;
- status must be one of `baseline`, `explore`, `active`, `retired`;
- `(dimension, value)` must be unique by schema expectation;
- at most one registry row across all dimensions may have status `explore`;
- every `explore` row must have a non-empty `parent_value`;
- an experiment parent must exist in the same registry dimension and must not be retired;
- baseline rows must not have experiment parents;
- retired rows must not appear as positive active strategy weights in their mapped strategy dimension.

Multiple pending `explore` experiments or orphan/retired parents are `failed` because the evolution and attribution logic assumes one controlled experiment at a time.

No pending experiment is valid and remains `ready`.

## 11. Cross-table strategy/registry consistency

Mapping is the existing production mapping:

- registry `hook` -> strategy `hook_type`
- registry `tone` -> strategy `style_type`
- registry `cta` -> strategy `cta_type`

For custom registry values represented in strategy stats:

- `retired` registry value must have zero strategy weight;
- `explore` registry value must not be exploitable and therefore must have zero strategy weight;
- an `active` custom value may have positive weight;
- missing strategy stats for an unseen/new registry value are acceptable.

Contradictory lifecycle/weight state is `failed`.

## 12. Shadow daily-plan verification

The verifier will build, but never save, one plan for the next Vietnam calendar day using:

- raw validated `AdaptiveConfig` converted to the existing model;
- current `category` strategy stats;
- current `time_bucket` strategy stats;
- existing `planner.build_daily_plan()`;
- a fixed `now` value passed into the planner so the test is deterministic.

The resulting in-memory slots must satisfy:

- at least one slot;
- all actions belong to `CORE_ACTIONS`;
- unique `slot_id` values;
- unique `planned_for` timestamps;
- chronological order;
- planned local times remain inside `SAFE_TIME_BUCKETS`;
- all slot statuses are `planned`;
- no claim/finished metadata is populated;
- strategy mode is one of `baseline`, `exploit`, `explore`, `retest`;
- if a slot is adaptive rather than baseline, it carries the current strategy version;
- slot count must never exceed `len(SAFE_TIME_BUCKETS)`;
- when adaptive category learning is active, slot count must stay inside the existing `target_daily_volume()` ±20% band around `baseline_daily_volume`;
- when planner falls back to baseline, a configured baseline volume that exceeds the available baseline template capacity is a failed configuration rather than silently accepting a shorter shadow plan.

The verifier records the planned slot count and local schedule in output only. It does not call `plan_repository.save_slots()`.

If the planner raises, emits duplicate/unsafe slots, or cannot satisfy its own safety properties, readiness is `failed`.

If the planner safely falls back to baseline because learning is insufficient, readiness is `degraded`, not failed.

## 13. Data-health checks that remain degraded, not failed

Phase 4N must distinguish lack of evidence from broken state.

Examples classified as `degraded`:

- adaptive is enabled but no learned current strategy exists yet;
- no last-good strategy has been proven yet;
- category learning does not meet planner maturity threshold;
- time-bucket learning does not meet planner maturity threshold;
- no pending style experiment exists is NOT degradation; it is normal/ready;
- format learning exists but is not consumed by selection is NOT a readiness failure because that feature is explicitly out of Phase 4N scope.

The gate does not re-run Facebook metric collection or external health checks. Existing `health` and `metrics` jobs own those responsibilities.

## 14. CLI output and failure semantics

`readiness_runner.py` prints a compact report suitable for GitHub Actions logs, for example:

```text
PHASE_4_READINESS: DEGRADED
[READY] schema — 8 required tables present
[READY] adaptive_config — id=1, baseline=12, explore=20%
[READY] strategy_versions — current=v8, last_good=v7
[READY] strategy_stats — 34 rows valid
[READY] style_registry — 17 rows, pending_explore=0
[DEGRADED] learning — time buckets not mature; shadow plan used baseline times
[READY] shadow_plan — 12 unique safe slots for 2026-09-01
```

On corruption:

```text
PHASE_4_READINESS: FAILED
[FAILED] strategy_versions — last_good pointer v7 has no snapshot
```

Exit code is non-zero only for aggregate `failed` or an unexpected dependency/query exception.

The runner must not swallow Turso errors as degradation. Connection/query failure is a hard failure because the verifier cannot establish readiness.

## 15. GitHub Actions workflow

Create `.github/workflows/production-readiness.yml` with:

- `workflow_dispatch` only;
- `permissions: contents: read`;
- Python 3.11;
- pinned SHA versions for `actions/checkout` and `actions/setup-python`, matching repository security practice;
- dependency installation from pinned `requirements.txt`;
- Turso production secrets only;
- command: `python readiness_runner.py`.

It MUST NOT contain `schedule:` and MUST NOT expose Facebook, Gemini, Pexels, or Telegram secrets because the readiness runner does not need them.

## 16. TDD and verification plan

Implementation follows red-green-refactor.

### RED group 1 — read-only adapter

Tests prove:

- SELECT works and fetches rows;
- allowed `PRAGMA table_info` works;
- UPDATE/INSERT/CREATE/ALTER/DELETE are rejected before connection execution;
- connection closes on success/failure;
- no commit occurs;
- transient Turso error uses existing retry behavior.

### RED group 2 — readiness invariants

Fixture-backed tests cover:

- fully healthy state => `ready`;
- missing table/column => `failed`;
- missing real adaptive config row => `failed`;
- dangling current/last-good pointer => `failed`;
- inconsistent last-good audit flag => `failed`;
- no current learned version => `degraded`;
- no last-good version yet => `degraded`;
- NaN/Infinity/negative strategy values => `failed`;
- suspended/retired positive weight => `failed`;
- unsafe active time bucket => `failed`;
- multiple pending experiments => `failed`;
- orphan experiment parent => `failed`;
- retired registry value with positive strategy weight => `failed`.

### RED group 3 — shadow planner and wiring

Tests prove:

- healthy adaptive state produces a deterministic safe in-memory plan;
- insufficient learning safely falls back and marks readiness degraded;
- duplicate/unsafe planner output fails closed;
- verifier never invokes any save/mutation function;
- readiness workflow is `workflow_dispatch` only;
- workflow passes only Turso secrets needed by the runner;
- production workflow remains unchanged.

### Final verification

Before Phase 4 closure:

1. full unit suite passes;
2. `python -m compileall -q .` passes;
3. PR diff contains only approved 4N/4N.0 scope;
4. PR CI passes on final HEAD;
5. PR is merged to `main` only after approval;
6. post-merge CI on `main` passes;
7. manual `Production Readiness` workflow is run using production Turso secrets;
8. the production readiness result is reviewed.

Phase 4 is declared closed only after step 8 produces `READY` or an explicitly accepted `DEGRADED` result with no `FAILED` checks. Any `FAILED` result requires remediation and another read-only readiness run.

## 17. Out of scope

Phase 4N explicitly does not:

- make `format_type` adaptive in planner/style selection;
- change scoring formulas;
- change learning thresholds;
- change rollback thresholds;
- add new content categories;
- add new posting schedules;
- run Facebook/Pexels/Gemini smoke calls;
- repair production state automatically;
- mutate DB state merely to make readiness pass;
- change branch-protection rules.

If readiness exposes existing corrupt state, remediation will be handled as a separate explicit, reviewed change. The verifier remains read-only.

## 18. Acceptance criteria

Phase 4N is complete when all of the following are true:

- standalone readiness runner exists and cannot issue write SQL through its DB adapter;
- manual-only readiness workflow exists;
- required schema/config/strategy/style invariants are checked;
- shadow planner reuses the production planner without persistence;
- failure/degraded/ready semantics are deterministic and tested;
- 4N.0 reporting and last-good audit fixes remain covered;
- full CI and compile gate are green;
- final production readiness run has no failed checks.
