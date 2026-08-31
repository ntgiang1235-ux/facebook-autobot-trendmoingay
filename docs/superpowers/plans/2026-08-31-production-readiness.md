# Phase 4N Production Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manual, read-only production readiness gate that validates Phase 4 adaptive state and builds a safe shadow plan without publishing or mutating Turso.

**Architecture:** Use a dedicated read-only Turso adapter (`app/readiness_db.py`) and a separate readiness engine (`app/readiness.py`) so operational hardening code cannot accidentally create schema or record jobs. A small `readiness_runner.py` prints structured results and a manual-only GitHub Actions workflow invokes it using only Turso secrets. The existing pure `app.planner.build_daily_plan()` remains the single planner implementation.

**Tech Stack:** Python 3.11, unittest, libSQL/Turso, GitHub Actions, existing `db_retry`, `planner`, `strategy_models`, and strategy/style schema.

**Spec:** `docs/superpowers/specs/2026-08-31-production-readiness-design.md`

## Global Constraints

- Readiness is strictly read-only against production Turso.
- Never call `db.ensure_schema()` or `db.record_job()` from readiness.
- Allowed database statements are only `SELECT ...` and `PRAGMA table_info(...)`.
- No `WITH` statements are allowed by the readiness DB adapter.
- Never call Facebook, Gemini, Pexels, or Telegram from readiness.
- The readiness workflow is `workflow_dispatch` only; it has no cron schedule.
- Aggregate status precedence is `failed` > `degraded` > `ready`.
- Corruption is always `failed`; insufficient learning evidence may be `degraded`.
- Shadow planning must reuse `app.planner.build_daily_plan()` and must never call `plan_repository.save_slots()`.
- `format_type` is validated but remains out of adaptive selection scope.
- Production workflow `.github/workflows/facebook-autobot.yml` must remain behaviorally unchanged.

---

### Task 1: Read-only Turso adapter

**Files:**
- Create: `app/readiness_db.py`
- Create: `tests/test_readiness_db.py`

**Interfaces:**
- Consumes: `db_retry.run_with_retry`, `libsql.connect`, environment variables `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN`.
- Produces: `validate_config() -> None`, `validate_read_query(query: str) -> str`, `_execute_read_once(query: str, params: tuple = ()) -> list[tuple]`, `execute_read(query: str, params: tuple = ()) -> list[tuple]`.

- [ ] **Step 1: Write failing adapter tests**

Create `tests/test_readiness_db.py` with a fake connection/cursor and tests equivalent to:

```python
class ReadinessDatabaseTests(unittest.TestCase):
    def test_select_and_table_info_are_allowed(self):
        self.assertEqual(readiness_db.validate_read_query(" SELECT 1"), "SELECT")
        self.assertEqual(
            readiness_db.validate_read_query("PRAGMA table_info(adaptive_config)"),
            "PRAGMA",
        )

    def test_mutating_statements_are_rejected_before_connect(self):
        for query in (
            "UPDATE adaptive_config SET adaptive_enabled = 0",
            "INSERT INTO adaptive_config(id) VALUES (1)",
            "DELETE FROM strategy_stats",
            "CREATE TABLE x(id INTEGER)",
            "ALTER TABLE strategy_stats ADD COLUMN x INTEGER",
            "DROP TABLE strategy_stats",
            "REPLACE INTO adaptive_config(id) VALUES (1)",
            "WITH x AS (SELECT 1) SELECT * FROM x",
        ):
            with self.subTest(query=query):
                with self.assertRaises(ValueError):
                    readiness_db.execute_read(query)
        self.assertEqual(self.connect_calls, [])

    def test_execute_read_never_commits_and_always_closes(self):
        rows = readiness_db.execute_read("SELECT 1")
        self.assertEqual(rows, [(1,)])
        self.assertEqual(self.connection.commit_calls, 0)
        self.assertEqual(self.connection.close_calls, 1)

    def test_connection_closes_when_execute_raises(self):
        self.connection.execute_error = RuntimeError("query failed")
        with self.assertRaisesRegex(RuntimeError, "query failed"):
            readiness_db.execute_read("SELECT 1")
        self.assertEqual(self.connection.close_calls, 1)

    def test_transient_transport_error_uses_existing_retry_wrapper(self):
        with mock.patch.object(readiness_db, "run_with_retry", side_effect=lambda fn: fn()) as retry:
            readiness_db.execute_read("SELECT 1")
        retry.assert_called_once()
```

Patch `readiness_db.TURSO_DATABASE_URL`, `readiness_db.TURSO_AUTH_TOKEN`, and `readiness_db.libsql.connect` inside `setUp()` so tests never use real production credentials.

- [ ] **Step 2: Run RED test**

Run:

```bash
python -m unittest tests.test_readiness_db -v
```

Expected: import/module failure because `app.readiness_db` does not exist.

- [ ] **Step 3: Implement minimal read-only adapter**

Create `app/readiness_db.py` with these exact enforcement rules:

```python
import os
import re

import libsql
from dotenv import load_dotenv

from db_retry import run_with_retry

load_dotenv()

TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL", "").strip()
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "").strip()
_COMMENT_PREFIX = re.compile(r"^(?:\s|--[^\n]*(?:\n|$)|/\*.*?\*/)*", re.S)


def validate_config() -> None:
    missing = []
    if not TURSO_DATABASE_URL:
        missing.append("TURSO_DATABASE_URL")
    if not TURSO_AUTH_TOKEN:
        missing.append("TURSO_AUTH_TOKEN")
    if missing:
        raise RuntimeError("Thiếu cấu hình Turso: " + ", ".join(missing))


def validate_read_query(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("readiness query must be a non-empty string")
    normalized = _COMMENT_PREFIX.sub("", query).lstrip()
    keyword = normalized.split(None, 1)[0].upper() if normalized else ""
    if keyword == "SELECT":
        return keyword
    if keyword == "PRAGMA" and re.match(
        r"(?is)^PRAGMA\s+table_info\s*\(\s*[A-Za-z_][A-Za-z0-9_]*\s*\)\s*;?\s*$",
        normalized,
    ):
        return keyword
    raise ValueError(f"readiness database rejected non-read query: {keyword or 'UNKNOWN'}")


def _execute_read_once(query: str, params: tuple = ()) -> list[tuple]:
    validate_read_query(query)
    validate_config()
    conn = libsql.connect(database=TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN)
    try:
        cur = conn.execute(query, params)
        return cur.fetchall()
    finally:
        conn.close()


def execute_read(query: str, params: tuple = ()) -> list[tuple]:
    validate_read_query(query)
    return run_with_retry(lambda: _execute_read_once(query, params))
```

Do not add `commit()` or transaction helpers.

- [ ] **Step 4: Run GREEN adapter tests**

Run:

```bash
python -m unittest tests.test_readiness_db -v
```

Expected: all adapter tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add app/readiness_db.py tests/test_readiness_db.py
git commit -m "feat: add read-only readiness database adapter"
```

---

### Task 2: Core readiness invariant engine

**Files:**
- Create: `app/readiness.py`
- Create: `tests/test_readiness.py`

**Interfaces:**
- Consumes: callable `execute_fn(query: str, params: tuple = ()) -> list[tuple]`, planner constants `CORE_ACTIONS`, `SAFE_TIME_BUCKETS`, and strategy/style schema.
- Produces: `ReadinessCheck`, `ReadinessResult`, `run_core_checks(execute_fn) -> list[ReadinessCheck]`, `aggregate_checks(checks) -> str`, and helpers that return structured checks instead of mutating state.

- [ ] **Step 1: Write failing invariant tests with a real in-memory SQLite fixture**

Use `sqlite3.connect(":memory:")` and a fixture that creates only the Phase 4 tables/columns from the approved spec. Expose an executor:

```python
def execute(self, query, params=()):
    return self.conn.execute(query, params).fetchall()
```

Seed a healthy state with:

```python
INSERT INTO adaptive_config VALUES (1, 1, 1, 1, 0.20, 12, 2, 1);
INSERT INTO strategy_versions VALUES
  (1, '{"category":{"post":0.4}}', '{"current_strategy_version":1}', '2026-08-01T00:00:00+00:00', 'proven', 1),
  (2, '{"category":{"post":0.4}}', '{"current_strategy_version":2}', '2026-08-31T00:00:00+00:00', 'refresh', 0);
```

Seed category and time-bucket stats with finite valid values and the baseline hook/tone/CTA registry rows.

Tests must include:

```python
def test_healthy_core_state_has_no_failed_checks(self):
    checks = readiness.run_core_checks(self.execute)
    self.assertNotIn("failed", {item.status for item in checks})


def test_missing_required_table_is_failed(self):
    self.conn.execute("DROP TABLE content_metrics")
    checks = readiness.run_core_checks(self.execute)
    self.assertCheck(checks, "schema", "failed")


def test_missing_real_config_row_is_failed(self):
    self.conn.execute("DELETE FROM adaptive_config")
    self.assertCheck(readiness.run_core_checks(self.execute), "adaptive_config", "failed")


def test_dangling_current_or_last_good_pointer_is_failed(self):
    self.conn.execute("UPDATE adaptive_config SET current_strategy_version = 999 WHERE id = 1")
    self.assertCheck(readiness.run_core_checks(self.execute), "strategy_versions", "failed")


def test_wrong_last_good_audit_flag_is_failed(self):
    self.conn.execute("UPDATE strategy_versions SET is_last_good = CASE version_id WHEN 1 THEN 0 ELSE 1 END")
    self.assertCheck(readiness.run_core_checks(self.execute), "strategy_versions", "failed")


def test_no_current_strategy_is_degraded_not_failed(self):
    self.conn.execute("UPDATE adaptive_config SET current_strategy_version = NULL WHERE id = 1")
    checks = readiness.run_core_checks(self.execute)
    self.assertCheck(checks, "strategy_versions", "degraded")


def test_no_last_good_strategy_is_degraded_not_failed(self):
    self.conn.execute("UPDATE adaptive_config SET last_good_strategy_version = NULL WHERE id = 1")
    self.conn.execute("UPDATE strategy_versions SET is_last_good = 0")
    self.assertCheck(readiness.run_core_checks(self.execute), "strategy_versions", "degraded")


def test_nonfinite_or_negative_strategy_values_are_failed(self):
    self.conn.execute("UPDATE strategy_stats SET current_weight = -0.1 WHERE dimension = 'category' AND value = 'post'")
    self.assertCheck(readiness.run_core_checks(self.execute), "strategy_stats", "failed")


def test_suspended_positive_weight_is_failed(self):
    self.conn.execute("UPDATE strategy_stats SET status='suspended', current_weight=0.2 WHERE dimension='category' AND value='post'")
    self.assertCheck(readiness.run_core_checks(self.execute), "strategy_stats", "failed")


def test_unsafe_active_time_bucket_is_failed(self):
    self.conn.execute("INSERT INTO strategy_stats VALUES (NULL,'time_bucket','23:30',5,60,60,0.8,0.2,NULL,'active',NULL,NULL,'2026-08-31T00:00:00+00:00')")
    self.assertCheck(readiness.run_core_checks(self.execute), "strategy_stats", "failed")


def test_multiple_pending_experiments_are_failed(self):
    self.conn.execute("INSERT INTO style_registry(dimension,value,parent_value,status,created_at) VALUES ('hook','question_alt','question','explore','2026-08-31T00:00:00+00:00')")
    self.conn.execute("INSERT INTO style_registry(dimension,value,parent_value,status,created_at) VALUES ('cta','save_alt','save_for_later','explore','2026-08-31T00:00:00+00:00')")
    self.assertCheck(readiness.run_core_checks(self.execute), "style_registry", "failed")


def test_orphan_experiment_parent_is_failed(self):
    self.conn.execute("INSERT INTO style_registry(dimension,value,parent_value,status,created_at) VALUES ('hook','question_alt','missing_parent','explore','2026-08-31T00:00:00+00:00')")
    self.assertCheck(readiness.run_core_checks(self.execute), "style_registry", "failed")


def test_retired_registry_value_with_positive_weight_is_failed(self):
    self.conn.execute("INSERT INTO style_registry(dimension,value,parent_value,status,created_at,retired_at) VALUES ('hook','question_alt','question','retired','2026-08-01T00:00:00+00:00','2026-08-30T00:00:00+00:00')")
    self.conn.execute("INSERT INTO strategy_stats VALUES (NULL,'hook_type','question_alt',5,60,60,0.8,0.2,NULL,'active',NULL,NULL,'2026-08-31T00:00:00+00:00')")
    self.assertCheck(readiness.run_core_checks(self.execute), "style_registry", "failed")
```

Also test `aggregate_checks()` precedence with explicit `ready/degraded/failed` fixtures.

- [ ] **Step 2: Run RED invariant tests**

Run:

```bash
python -m unittest tests.test_readiness -v
```

Expected: module/import failure because `app.readiness` does not exist.

- [ ] **Step 3: Implement structured result models and schema/config checks**

Start `app/readiness.py` with:

```python
from dataclasses import dataclass
import json
import math

from app.planner import CORE_ACTIONS, SAFE_TIME_BUCKETS

READY = "ready"
DEGRADED = "degraded"
FAILED = "failed"

@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    status: str
    detail: str

@dataclass(frozen=True)
class ReadinessResult:
    status: str
    checks: tuple[ReadinessCheck, ...]


def aggregate_checks(checks):
    statuses = {item.status for item in checks}
    if FAILED in statuses:
        return FAILED
    if DEGRADED in statuses:
        return DEGRADED
    return READY
```

Implement `_schema_check()` using `SELECT name FROM sqlite_master WHERE type = 'table'` plus `PRAGMA table_info(<static table name>)`. The required table and column sets must be literal constants copied from the spec.

Implement `_load_raw_config()` with:

```sql
SELECT id, adaptive_enabled, auto_schedule_enabled, auto_suspend_enabled,
       exploration_rate, baseline_daily_volume,
       current_strategy_version, last_good_strategy_version
FROM adaptive_config
ORDER BY id
```

Require exactly one row and `id == 1`; validate booleans, finite exploration rate `[0,1]`, positive integer baseline, and positive integer nullable pointers.

- [ ] **Step 4: Implement strategy-version/stat/style checks minimally to satisfy RED cases**

Use static `SELECT` queries only. Parse `weights_json` and `config_json` via `json.loads`, require dictionaries, and validate current snapshot self-pointer when present. Validate numeric stat fields using `math.isfinite(float(value))` before ranges. Use this exact registry mapping:

```python
REGISTRY_TO_STAT = {
    "hook": "hook_type",
    "tone": "style_type",
    "cta": "cta_type",
}
```

Unknown registry dimensions/statuses, more than one `explore`, missing/same-dimension-retired experiment parents, and positive weights for registry `explore`/`retired` values are failures.

Implement:

```python
def run_core_checks(execute_fn):
    checks = []
    schema = _schema_check(execute_fn)
    checks.append(schema)
    if schema.status == FAILED:
        return checks
    config_check, config = _config_check(execute_fn)
    checks.append(config_check)
    if config is None:
        return checks
    checks.append(_strategy_versions_check(execute_fn, config))
    checks.append(_strategy_stats_check(execute_fn))
    checks.append(_style_registry_check(execute_fn))
    return checks
```

- [ ] **Step 5: Run GREEN invariant tests**

Run:

```bash
python -m unittest tests.test_readiness -v
```

Expected: all core invariant tests pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add app/readiness.py tests/test_readiness.py
git commit -m "feat: validate adaptive production readiness state"
```

---

### Task 3: Shadow planner and CLI runner

**Files:**
- Modify: `app/readiness.py`
- Create: `readiness_runner.py`
- Create: `tests/test_readiness_shadow_plan.py`
- Create: `tests/test_readiness_runner.py`

**Interfaces:**
- Consumes: validated raw config, `strategy_models.AdaptiveConfig`, `strategy_models.StrategyStat`, `planner.build_daily_plan`, `planner.VIETNAM_TZ`, and `readiness_db.execute_read`.
- Produces: `run_readiness(execute_fn, *, now=None, planner_fn=build_daily_plan) -> ReadinessResult`, shadow-plan check named `shadow_plan`, learning check named `learning`, `format_result(result) -> str`, and CLI `main() -> int`.

- [ ] **Step 1: Write failing shadow-plan tests**

Use the healthy SQLite fixture from Task 2 and a fixed UTC time `datetime(2026, 8, 31, 2, 0, tzinfo=timezone.utc)`.

Tests must cover:

```python
def test_healthy_adaptive_state_builds_safe_shadow_plan_without_writes(self):
    writes = []
    def guarded_execute(query, params=()):
        if query.lstrip().split(None, 1)[0].upper() != "SELECT" and not query.lstrip().upper().startswith("PRAGMA"):
            writes.append(query)
        return self.execute(query, params)
    result = readiness.run_readiness(guarded_execute, now=self.now)
    self.assertEqual(result.status, "ready")
    self.assertEqual(writes, [])
    shadow = self.get_check(result, "shadow_plan")
    self.assertEqual(shadow.status, "ready")


def test_insufficient_learning_uses_safe_baseline_and_is_degraded(self):
    self.conn.execute("UPDATE strategy_stats SET sample_count = 0")
    result = readiness.run_readiness(self.execute, now=self.now)
    self.assertEqual(result.status, "degraded")
    self.assertEqual(self.get_check(result, "learning").status, "degraded")
    self.assertEqual(self.get_check(result, "shadow_plan").status, "ready")


def test_duplicate_or_unsafe_planner_output_fails_closed(self):
    duplicate = DailyPlanSlot(
        plan_date="2026-09-01", slot_id="0830-post-01",
        planned_for="2026-09-01T01:30:00+00:00", action="post", category="post",
        strategy_mode="exploit", strategy_version=2, status="planned",
        claim_run_key=None, claimed_at=None, finished_at=None, detail="",
        created_at=self.now.isoformat(),
    )
    result = readiness.run_readiness(
        self.execute,
        now=self.now,
        planner_fn=lambda *args, **kwargs: [duplicate, duplicate],
    )
    self.assertEqual(self.get_check(result, "shadow_plan").status, "failed")
```

Also inject planner output at local `23:30` and require failure.

- [ ] **Step 2: Write failing CLI tests**

Create `tests/test_readiness_runner.py` that patches `readiness_runner.run_readiness` and verifies:

```python
def test_ready_and_degraded_exit_zero(self):
    for status in ("ready", "degraded"):
        with self.subTest(status=status):
            result = ReadinessResult(status, (ReadinessCheck("schema", "ready", "ok"),))
            with mock.patch.object(readiness_runner, "run_readiness", return_value=result):
                self.assertEqual(readiness_runner.main(), 0)


def test_failed_exits_one(self):
    result = ReadinessResult("failed", (ReadinessCheck("schema", "failed", "missing"),))
    with mock.patch.object(readiness_runner, "run_readiness", return_value=result):
        self.assertEqual(readiness_runner.main(), 1)


def test_query_exception_exits_one(self):
    with mock.patch.object(readiness_runner, "run_readiness", side_effect=RuntimeError("Turso unavailable")):
        self.assertEqual(readiness_runner.main(), 1)
```

Capture stdout and assert the header is `PHASE_4_READINESS: READY|DEGRADED|FAILED` and each line uses `[STATUS] name — detail`.

- [ ] **Step 3: Run RED shadow/runner tests**

Run:

```bash
python -m unittest tests.test_readiness_shadow_plan tests.test_readiness_runner -v
```

Expected: missing `run_readiness`/runner failures.

- [ ] **Step 4: Implement model conversion and learning readiness**

In `app/readiness.py`, add loaders converting SQL rows into existing `AdaptiveConfig` and `StrategyStat` objects. Use planner maturity constants rather than duplicate magic numbers where they are importable. Learning status is degraded when category maturity or time-bucket maturity is insufficient; it is ready otherwise.

- [ ] **Step 5: Implement shadow-plan validation**

`run_readiness()` must:

1. execute `run_core_checks()`;
2. return immediately with aggregate `failed` when core corruption prevents safe model construction;
3. build next Vietnam calendar day's plan with the existing planner and the supplied fixed/current `now`;
4. validate non-empty slots, allowed actions, unique slot IDs, unique timestamps, chronological order, safe local time buckets, `planned` status, empty claim/finish metadata, allowed strategy modes, and current strategy version on non-baseline slots;
5. validate target volume against planner guardrails: baseline fallback must equal `baseline_daily_volume`; adaptive volume must be within `ceil(baseline*0.80)` and `floor(baseline*1.20)` with upper never below lower;
6. append `learning` and `shadow_plan` checks and aggregate.

Do not import or call `save_slots`.

- [ ] **Step 6: Implement CLI runner**

Create `readiness_runner.py`:

```python
from app.readiness import run_readiness
from app.readiness_db import execute_read


def format_result(result):
    lines = [f"PHASE_4_READINESS: {result.status.upper()}"]
    lines.extend(
        f"[{item.status.upper()}] {item.name} — {item.detail}"
        for item in result.checks
    )
    return "\n".join(lines)


def main():
    try:
        result = run_readiness(execute_read)
    except Exception as error:
        print("PHASE_4_READINESS: FAILED")
        print(f"[FAILED] dependency — {error}")
        return 1
    print(format_result(result))
    return 1 if result.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: Run GREEN shadow/runner tests**

Run:

```bash
python -m unittest tests.test_readiness_shadow_plan tests.test_readiness_runner -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit Task 3**

```bash
git add app/readiness.py readiness_runner.py tests/test_readiness_shadow_plan.py tests/test_readiness_runner.py
git commit -m "feat: add Phase 4 shadow readiness verification"
```

---

### Task 4: Manual workflow, wiring tests, and final verification

**Files:**
- Create: `.github/workflows/production-readiness.yml`
- Create: `tests/test_readiness_workflow.py`
- Modify: `docs/superpowers/specs/2026-08-31-production-readiness-design.md` only if implementation exposes a factual mismatch; do not weaken safety requirements.

**Interfaces:**
- Consumes: `readiness_runner.py`, repository pinned action SHAs already used by `.github/workflows/ci.yml` / production workflow.
- Produces: a manual GitHub Actions workflow named `Production Readiness`.

- [ ] **Step 1: Write failing workflow tests**

Create `tests/test_readiness_workflow.py` reading workflow text and asserting:

```python
def test_readiness_workflow_is_manual_only(self):
    text = Path(".github/workflows/production-readiness.yml").read_text()
    self.assertIn("workflow_dispatch:", text)
    self.assertNotIn("schedule:", text)


def test_readiness_workflow_only_exposes_turso_secrets(self):
    text = Path(".github/workflows/production-readiness.yml").read_text()
    self.assertIn("TURSO_DATABASE_URL", text)
    self.assertIn("TURSO_AUTH_TOKEN", text)
    for forbidden in ("FB_ACCESS_TOKEN", "GEMINI", "PEXELS", "TELEGRAM"):
        self.assertNotIn(forbidden, text)


def test_readiness_workflow_runs_standalone_runner(self):
    text = Path(".github/workflows/production-readiness.yml").read_text()
    self.assertIn("python readiness_runner.py", text)
    self.assertNotIn("hardening_runner.py", text)
```

Also read `.github/workflows/facebook-autobot.yml` and assert it does not mention `readiness_runner.py` or `phase4_verify`.

- [ ] **Step 2: Run RED workflow test**

Run:

```bash
python -m unittest tests.test_readiness_workflow -v
```

Expected: file-not-found failure because the readiness workflow does not yet exist.

- [ ] **Step 3: Create manual-only workflow**

Create `.github/workflows/production-readiness.yml` using the same pinned `actions/checkout` and `actions/setup-python` SHAs already present in repo:

```yaml
name: Production Readiness

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  verify:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    env:
      TURSO_DATABASE_URL: ${{ secrets.TURSO_DATABASE_URL }}
      TURSO_AUTH_TOKEN: ${{ secrets.TURSO_AUTH_TOKEN }}
    steps:
      - name: Checkout
        uses: actions/checkout@<repo-pinned-sha>
      - name: Setup Python
        uses: actions/setup-python@<repo-pinned-sha>
        with:
          python-version: "3.11"
          cache: pip
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
      - name: Verify Phase 4 production readiness
        run: python readiness_runner.py
```

Replace the two placeholders in this plan during implementation with the exact existing repository SHAs; no floating tags are permitted in the actual workflow.

- [ ] **Step 4: Run GREEN workflow tests**

Run:

```bash
python -m unittest tests.test_readiness_workflow -v
```

Expected: all workflow tests pass.

- [ ] **Step 5: Run complete Phase 4N verification locally/CI-equivalent**

Run:

```bash
python -m unittest discover -s tests -v
python -m compileall -q .
```

Expected: zero failures/errors and compile exit code 0.

- [ ] **Step 6: Inspect PR scope**

Compare `main...codex/phase-4n-production-readiness`. Confirm changed files are limited to:

- 4N.0 reporting/strategy-guard cleanup and its regression test;
- 4N spec and implementation plan;
- readiness DB/engine/runner/tests/workflow.

Confirm `.github/workflows/facebook-autobot.yml` has no Phase 4N production scheduling change.

- [ ] **Step 7: Commit Task 4**

```bash
git add .github/workflows/production-readiness.yml tests/test_readiness_workflow.py
git commit -m "ci: add manual Phase 4 production readiness gate"
```

- [ ] **Step 8: Final PR verification before merge approval**

Wait for PR CI on the final HEAD and confirm:

- full unittest step success;
- Python compile step success;
- PR remains mergeable;
- no unresolved review thread identifies a correctness or safety defect.

Do not merge until explicit user approval.

---

## Self-Review

### Spec coverage

- Strict read-only DB enforcement: Task 1.
- Schema/config validation: Task 2.
- Version pointers/last-good audit consistency: Task 2.
- Strategy numeric/status invariants: Task 2.
- Style registry and cross-table lifecycle consistency: Task 2.
- READY/DEGRADED/FAILED semantics: Tasks 2 and 3.
- Shadow planner with no persistence: Task 3.
- Manual-only workflow with Turso-only secrets: Task 4.
- Full test/compile/PR verification: Task 4.
- No adaptive format selection or other Phase 4 scope expansion: Global Constraints.

### Placeholder scan

The implementation plan contains angle-bracket placeholders only inside the illustrative workflow snippet. Task 4 explicitly requires replacing them with exact SHAs already used by the repository before creating the actual workflow. No production code/test step permits placeholders.

### Type consistency

- `execute_read(query: str, params: tuple = ()) -> list[tuple]` is the executor used throughout.
- Core and final result objects consistently use `ReadinessCheck` and `ReadinessResult`.
- `run_core_checks()` returns checks; `run_readiness()` returns the aggregate result.
- Shadow planning uses the existing `AdaptiveConfig`, `StrategyStat`, and `DailyPlanSlot` types.
