# Phase 4N Production Readiness Implementation Plan

**Goal:** Add a manual, read-only production readiness gate that validates Phase 4 adaptive state and builds a safe next-day shadow plan without publishing or mutating Turso.

**Architecture:** Dedicated `app/readiness_db.py` + `app/readiness.py` + standalone `readiness_runner.py`; manual-only `.github/workflows/production-readiness.yml`; reuse `app.planner.build_daily_plan()` rather than duplicating planner logic.

**Spec:** `docs/superpowers/specs/2026-08-31-production-readiness-design.md`

## Global constraints

- Readiness is strictly read-only against production Turso.
- No `ensure_schema`, `record_job`, publication, Telegram, Gemini, Pexels, or Facebook call.
- SQL allowlist is only `SELECT` and exact `PRAGMA table_info(...)`; no generic `WITH` or stacked statements.
- Workflow is `workflow_dispatch` only.
- `failed > degraded > ready` precedence.
- Corruption is `failed`; insufficient maturity may be `degraded`.
- Shadow planning reuses production planner and never calls `save_slots`.
- `format_type` is validated but is not made adaptive in this phase.
- Phase 4M's append-only strategy history is preserved: `adaptive_config.last_good_strategy_version` is canonical; historical `strategy_versions.is_last_good` values are not mutated or treated as the current pointer.

---

## Task 1 — Read-only Turso adapter

**Files**
- `app/readiness_db.py`
- `tests/test_readiness_db.py`

- [x] Add RED tests for missing module and read-only contract.
- [x] Allow `SELECT` and exact `PRAGMA table_info(identifier)`.
- [x] Reject UPDATE/INSERT/DELETE/CREATE/ALTER/DROP/REPLACE/WITH.
- [x] Reject malformed PRAGMAs and stacked statements before execution.
- [x] Never call `commit()`.
- [x] Close connections on success/failure.
- [x] Reuse existing transient `run_with_retry` behavior.
- [x] Verify GREEN with full CI.

Acceptance evidence is the CI sequence where the adapter RED failed on the missing module and the following GREEN run passed unit tests and compile.

---

## Task 2 — Core readiness invariants

**Files**
- `app/readiness.py`
- `tests/test_readiness.py`

- [x] Add structured `ReadinessCheck` / `ReadinessResult` models.
- [x] Add aggregate precedence `failed > degraded > ready`.
- [x] Validate the 8 required Phase 4 runtime tables and required columns.
- [x] Read raw `adaptive_config` and require exactly `id=1`.
- [x] Validate kill switches, exploration rate, baseline volume and version pointers.
- [x] Validate current/last-good canonical pointer targets.
- [x] Parse every strategy snapshot JSON object and validate current snapshot self-pointer.
- [x] Preserve Phase 4M append-only semantics: historical `is_last_good` is validated as stored 0/1 metadata only; it does not override or have to equal the canonical config pointer.
- [x] Validate canonical strategy dimensions, finite/ranged numbers, non-negative weights and zero weight for suspended/retired values.
- [x] Validate planner categories and safe time buckets.
- [x] Validate style registry dimensions/statuses, single pending explore experiment and parent integrity.
- [x] Validate registry lifecycle versus exploitable strategy weights.
- [x] Verify corruption fixtures fail closed and warm-up states degrade safely.

### Code-review correction

An earlier 4N.0 draft attempted to synchronize `strategy_versions.is_last_good` with UPDATE statements. Final review found this violated the already-approved Phase 4M rule that `strategy_versions` is append-only and the config pointer is canonical.

The corrective TDD contract is:

```python
# Healthy promotion moves only the canonical pointer.
assert result.status == "promoted_last_good"
assert config.last_good_strategy_version == current_version
assert historical_snapshot_flags_are_unchanged

# Readiness trusts the canonical pointer, not immutable historical flags.
assert readiness_strategy_versions_check.status == "ready"
```

- [x] Write these regression expectations before the code correction.
- [x] Confirm RED contains only the two intended last-good failures.
- [x] Restore `strategy_guard.py` append-only behavior.
- [x] Remove historical flag/pointer equality from readiness semantics.
- [ ] Confirm final GREEN CI after the correction.

---

## Task 3 — Learning readiness, shadow planner and CLI

**Files**
- `app/readiness.py`
- `readiness_runner.py`
- `tests/test_readiness_shadow_plan.py`
- `tests/test_readiness_runner.py`

- [x] Convert validated DB rows to existing `AdaptiveConfig` / `StrategyStat` models.
- [x] Reuse planner maturity checks for category/time learning.
- [x] Mark insufficient maturity `degraded`, not corrupt.
- [x] Build next Vietnam calendar day's plan with production `build_daily_plan()`.
- [x] Validate actions/categories, plan date, unique IDs/timestamps, chronological order and safe local half-hour buckets.
- [x] Validate `planned` state and empty claim/finish metadata.
- [x] Validate strategy modes and adaptive strategy version metadata.
- [x] Validate baseline/adaptive volume guardrails.
- [x] Fail closed on planner exceptions, duplicate output or unsafe times.
- [x] Confirm verifier does not call any persistence function.
- [x] Add CLI output `PHASE_4_READINESS: READY|DEGRADED|FAILED`.
- [x] Exit 1 on failed/dependency exception; exit 0 on ready/degraded.
- [x] Verify RED then GREEN through CI.

---

## Task 4 — Manual production-readiness workflow

**Files**
- `.github/workflows/production-readiness.yml`
- `tests/test_readiness_workflow.py`

- [x] Add RED workflow tests before creating the workflow.
- [x] Create `Production Readiness` workflow with `workflow_dispatch` only.
- [x] Use `permissions: contents: read`.
- [x] Use pinned checkout/setup-python SHAs already used by the repo.
- [x] Install pinned `requirements.txt` dependencies.
- [x] Expose only `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN`.
- [x] Run `python readiness_runner.py`.
- [x] Prove production `facebook-autobot.yml` remains separate and unchanged.
- [x] Verify RED then GREEN through CI.

---

## Task 5 — Final review and merge gate

- [x] Inspect PR changed-file scope; only 4N.0 reporting cleanup, 4N verifier/workflow/tests/docs, and the temporary strategy-guard review correction area are involved.
- [x] Detect and correct the append-only `is_last_good` design regression before merge.
- [ ] Run/confirm final full unittest suite on the corrected final HEAD.
- [ ] Confirm `python -m compileall -q .` on the corrected final HEAD.
- [ ] Reinspect final PR diff for any remaining write path in readiness.
- [ ] Confirm PR #22 remains mergeable and has no unresolved important review issue.
- [ ] Ask for explicit user approval before merge.

Do not merge automatically at this gate.

---

## Post-merge closure procedure

After explicit merge approval:

- [ ] Merge PR #22 into `main`.
- [ ] Confirm `main` HEAD equals the merge result.
- [ ] Confirm post-merge CI succeeds on `main`.
- [ ] Manually run the new `Production Readiness` workflow against production Turso secrets.
- [ ] Review every readiness check.
- [ ] If any check is `FAILED`, do not close Phase 4; remediate separately and rerun the read-only verifier.
- [ ] If result is `READY`, close Phase 4.
- [ ] If result is only `DEGRADED`, close Phase 4 only if the degradation is documented lack of learning maturity/evidence rather than corruption.

## Out of scope

- adaptive `format_type` selection;
- scoring/learning/rollback threshold changes;
- new categories or posting schedules;
- automatic production repair;
- branch-protection changes;
- external API smoke calls in the readiness workflow.
