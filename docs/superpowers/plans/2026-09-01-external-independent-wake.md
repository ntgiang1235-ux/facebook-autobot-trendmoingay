# Phase 4N.4 External Independent Wake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an external wake path that triggers the existing Turso-authoritative dispatcher through GitHub `workflow_dispatch`, removing GitHub cron delivery as the only wake source without creating a second publisher or scheduling authority.

**Architecture:** Add one repository workflow, `External Dispatch Wake`, whose only trigger is `workflow_dispatch` and whose only application action is `python hardening_runner.py "dispatch"`. A cron-job.org job will POST to GitHub's workflow-dispatch endpoint at `:12` and `:42` each hour using a least-privilege GitHub token; GitHub continues to hold all Facebook/Turso/application secrets, and Turso `daily_plan` continues to decide whether a slot is due.

**Tech Stack:** GitHub Actions, Python 3.11, `unittest`, GitHub REST API, Turso/libSQL, cron-job.org.

**Spec:** `docs/superpowers/specs/2026-09-01-external-independent-wake-design.md`

## Global Constraints

- Turso `daily_plan` remains the only scheduling authority for publish slots.
- The external scheduler is a wake source only and must never choose a content category or slot.
- The repository-side external workflow must use `workflow_dispatch` only; it must not contain a `schedule:` trigger.
- The runtime command must be exactly `python hardening_runner.py "dispatch"`.
- The external workflow must not define `SCHEDULED_CRON`.
- The external workflow must use concurrency group `facebook-autobot-external-dispatch-wake` with `cancel-in-progress: false`.
- No planner, direct Facebook publishing script, generic externally supplied action, catch-up loop, or backfill logic may be introduced.
- Existing primary GitHub schedules and `.github/workflows/dispatch-watchdog.yml` remain unchanged in this phase.
- Facebook, Gemini, Telegram, Pexels, affiliate, and Turso credentials remain inside GitHub Actions and are never copied to cron-job.org.
- cron-job.org stores only the minimum GitHub credential required to dispatch this one repository workflow.
- Production readiness is read-only during verification; repository CI success alone does not prove external runtime readiness.

---

## File Structure

- Create `.github/workflows/external-dispatch-wake.yml`: dedicated receiver for externally initiated `workflow_dispatch` runs; no cron logic.
- Create `tests/test_external_dispatch_wake_workflow.py`: static contract tests that prevent the receiver from becoming a second scheduler or arbitrary-action entry point.
- Do not modify `hardening_runner.py`, dispatcher code, planner code, publisher code, or existing GitHub cron workflows unless a failing contract test proves the approved design cannot be implemented without doing so.

---

### Task 1: Add the External Wake Contract Test in RED

**Files:**
- Create: `tests/test_external_dispatch_wake_workflow.py`
- Reference only: `.github/workflows/dispatch-watchdog.yml`
- Reference only: `docs/superpowers/specs/2026-09-01-external-independent-wake-design.md`

**Interfaces:**
- Consumes: repository workflow text at `.github/workflows/external-dispatch-wake.yml`.
- Produces: a `unittest.TestCase` contract that later implementation must satisfy.

- [ ] **Step 1: Create the failing test file**

```python
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "external-dispatch-wake.yml"


class ExternalDispatchWakeWorkflowTest(unittest.TestCase):
    def workflow_text(self) -> str:
        self.assertTrue(WORKFLOW.exists(), "external dispatch wake workflow must exist")
        return WORKFLOW.read_text(encoding="utf-8")

    def test_external_wake_is_manual_dispatch_only(self):
        text = self.workflow_text()
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("schedule:", text)

    def test_external_wake_has_isolated_non_cancelling_concurrency(self):
        text = self.workflow_text()
        self.assertIn("group: facebook-autobot-external-dispatch-wake", text)
        self.assertIn("cancel-in-progress: false", text)

    def test_external_wake_runs_only_dispatch(self):
        text = self.workflow_text()
        self.assertIn('run: python hardening_runner.py "dispatch"', text)
        self.assertNotIn('hardening_runner.py "planner"', text)
        self.assertNotIn("ACTION:", text)
        self.assertNotIn("inputs:", text)

    def test_external_wake_does_not_inherit_github_schedule_staleness_gate(self):
        text = self.workflow_text()
        self.assertNotIn("SCHEDULED_CRON", text)

    def test_external_wake_wires_existing_runtime_secrets(self):
        text = self.workflow_text()
        required = (
            "GEMINI_API_KEYS",
            "FB_ACCESS_TOKEN",
            "FB_PAGE_ID",
            "TELEGRAM_TOKEN",
            "TELEGRAM_CHAT_ID",
            "BLACKLIST_WORDS",
            "AFFILIATE_LINK",
            "PEXELS_API_KEY",
            "TURSO_DATABASE_URL",
            "TURSO_AUTH_TOKEN",
        )
        for secret in required:
            self.assertIn(f"{secret}: ${{{{ secrets.{secret} }}}}", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python -m unittest tests.test_external_dispatch_wake_workflow -v
```

Expected: FAIL because `.github/workflows/external-dispatch-wake.yml` does not exist. Do not weaken the test to make RED pass.

- [ ] **Step 3: Run the full suite once to distinguish the intentional RED from unrelated failures**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: existing tests pass; only the new external-wake contract fails because the workflow is absent. If unrelated failures appear, stop and investigate them before implementation.

- [ ] **Step 4: Commit the RED test**

```bash
git add tests/test_external_dispatch_wake_workflow.py
git commit -m "test: define external dispatch wake contract"
```

Reviewer gate: confirm the test asserts the approved security/scheduling contract and does not test implementation trivia unrelated to the spec.

---

### Task 2: Implement the Minimal External Dispatch Wake Workflow in GREEN

**Files:**
- Create: `.github/workflows/external-dispatch-wake.yml`
- Test: `tests/test_external_dispatch_wake_workflow.py`

**Interfaces:**
- Consumes: existing `hardening_runner.py` action `dispatch`; existing GitHub repository secrets used by `dispatch-watchdog.yml`.
- Produces: GitHub Actions workflow named `External Dispatch Wake`, invokable only by `workflow_dispatch`.

- [ ] **Step 1: Create the minimal workflow**

```yaml
name: External Dispatch Wake

on:
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: facebook-autobot-external-dispatch-wake
  cancel-in-progress: false

jobs:
  dispatch:
    runs-on: ubuntu-latest
    timeout-minutes: 20

    env:
      TZ: Asia/Ho_Chi_Minh
      GEMINI_API_KEYS: ${{ secrets.GEMINI_API_KEYS }}
      FB_ACCESS_TOKEN: ${{ secrets.FB_ACCESS_TOKEN }}
      FB_PAGE_ID: ${{ secrets.FB_PAGE_ID }}
      TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
      TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
      BLACKLIST_WORDS: ${{ secrets.BLACKLIST_WORDS }}
      AFFILIATE_LINK: ${{ secrets.AFFILIATE_LINK }}
      PEXELS_API_KEY: ${{ secrets.PEXELS_API_KEY }}
      TURSO_DATABASE_URL: ${{ secrets.TURSO_DATABASE_URL }}
      TURSO_AUTH_TOKEN: ${{ secrets.TURSO_AUTH_TOKEN }}

    steps:
      - name: Checkout
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7

      - name: Setup Python
        uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7
        with:
          python-version: "3.11"
          cache: "pip"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run external dispatch wake
        run: python hardening_runner.py "dispatch"
```

Do not add workflow inputs, `schedule:`, planner calls, or a reusable generic action variable.

- [ ] **Step 2: Run the focused test and verify GREEN**

Run:

```bash
python -m unittest tests.test_external_dispatch_wake_workflow -v
```

Expected: all tests in the file PASS.

- [ ] **Step 3: Run repository verification**

Run exactly the same checks as CI:

```bash
python -m unittest discover -s tests -v
python -m compileall -q .
```

Expected: both commands exit 0.

- [ ] **Step 4: Review the workflow against the spec before committing**

Verify all of these literal properties:

```text
workflow_dispatch: present
schedule: absent
SCHEDULED_CRON: absent
inputs: absent
ACTION: absent
hardening_runner.py "dispatch": exactly one application invocation
hardening_runner.py "planner": absent
concurrency group: facebook-autobot-external-dispatch-wake
cancel-in-progress: false
```

- [ ] **Step 5: Commit the GREEN implementation**

```bash
git add .github/workflows/external-dispatch-wake.yml tests/test_external_dispatch_wake_workflow.py
git commit -m "feat: add external dispatch wake receiver"
```

Reviewer gate: confirm this workflow is only a receiver and does not duplicate scheduling or publishing logic.

---

### Task 3: CI, Pull Request, Review, and Merge

**Files:**
- No new production files expected.
- Review: `.github/workflows/external-dispatch-wake.yml`
- Review: `tests/test_external_dispatch_wake_workflow.py`
- Review: `docs/superpowers/specs/2026-09-01-external-independent-wake-design.md`

**Interfaces:**
- Consumes: GREEN branch from Task 2.
- Produces: merged workflow on default branch `main`, which is required before GitHub will accept dispatches by workflow filename on the production branch.

- [ ] **Step 1: Push the implementation branch and open a non-draft PR**

PR title:

```text
feat: add external independent dispatch wake
```

PR body must state:

```text
Adds a workflow_dispatch-only receiver for an external wake source.
Turso daily_plan remains the scheduling authority.
No planner, backfill, direct publisher, or new application secrets are exposed externally.
External provider rollout is intentionally performed only after this workflow reaches main.
```

- [ ] **Step 2: Wait for CI and inspect the exact checks**

The repo CI contract is:

```bash
python -m unittest discover -s tests -v
python -m compileall -q .
```

Expected: CI conclusion `success` on the PR head SHA.

- [ ] **Step 3: Perform code review**

Reject the PR if review finds any of these:

```text
schedule: in external-dispatch-wake.yml
SCHEDULED_CRON in external-dispatch-wake.yml
workflow inputs that select arbitrary actions
planner invocation
content-category invocation
Facebook API invocation outside existing Python path
new external-service secret embedded in repository text
weakened or deleted contract assertions
```

- [ ] **Step 4: Merge with expected head SHA and verify post-merge CI**

Merge only the reviewed head SHA. Then verify `main` contains `.github/workflows/external-dispatch-wake.yml` and the post-merge CI run succeeds.

- [ ] **Step 5: Record the production merge SHA**

This SHA is the baseline for runtime verification. Do not claim `EXTERNAL_WAKE_RUNTIME_READY` yet.

---

### Task 4: Provision the External cron-job.org Wake

**Files:**
- No repository source changes.
- External configuration: cron-job.org job.
- GitHub configuration: one fine-grained personal access token scoped only to `ntgiang1235-ux/facebook-autobot-trendmoingay` with Actions write and minimum required repository metadata/read access.

**Interfaces:**
- Consumes: merged `.github/workflows/external-dispatch-wake.yml` on `main`.
- Produces: authenticated POST requests to GitHub's workflow-dispatch endpoint at `:12` and `:42` each hour.

- [ ] **Step 1: Create a least-privilege GitHub credential**

Configure a fine-grained token with:

```text
Repository access: Only selected repositories
Selected repository: ntgiang1235-ux/facebook-autobot-trendmoingay
Repository permissions:
  Actions: Read and write
  Metadata: Read-only (GitHub-required baseline)
```

Do not grant access to other repositories. Do not place this token in repo files, GitHub workflow YAML, query strings, screenshots, chat logs, or shell history committed to source control.

- [ ] **Step 2: Create the cron-job.org HTTP job**

Configure:

```text
Title: Facebook AutoBot External Dispatch Wake
URL: https://api.github.com/repos/ntgiang1235-ux/facebook-autobot-trendmoingay/actions/workflows/external-dispatch-wake.yml/dispatches
Method: POST
Timezone: Asia/Ho_Chi_Minh
Minutes: 12,42
Hours: every hour
Days/months/weekdays: every
```

Custom headers:

```text
Authorization: Bearer <fine-grained GitHub token>
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```

Request body:

```json
{"ref":"main"}
```

Enable execution history. The expected GitHub API response for an accepted workflow dispatch is HTTP `204 No Content`.

- [ ] **Step 3: Confirm external-secret isolation**

Before enabling the schedule, verify cron-job.org contains none of these values:

```text
FB_ACCESS_TOKEN
FB_PAGE_ID
TURSO_DATABASE_URL
TURSO_AUTH_TOKEN
GEMINI_API_KEYS
TELEGRAM_TOKEN
TELEGRAM_CHAT_ID
PEXELS_API_KEY
AFFILIATE_LINK
BLACKLIST_WORDS
```

Only the fine-grained GitHub dispatch token may be stored there.

- [ ] **Step 4: Perform one provider test execution**

Use cron-job.org's test-run function once after the workflow exists on `main`.

Expected external result:

```text
HTTP 204 from GitHub workflow dispatch API
```

Immediately locate the corresponding GitHub Actions run and confirm:

```text
workflow name: External Dispatch Wake
event: workflow_dispatch
head_branch/ref: main
conclusion: success, or still running while being observed
```

A provider HTTP `401`, `403`, `404`, or `422` is configuration/authentication failure and must be corrected before enabling the recurring schedule.

- [ ] **Step 5: Enable recurring `:12/:42` execution**

Do not create a higher-frequency job. The external provider is a wake source, not a slot scheduler.

---

### Task 5: Production Runtime Verification and Completion Gate

**Files:**
- No production mutation files.
- Read-only verification: GitHub Actions run/job logs and existing Phase 4 readiness command/workflow.

**Interfaces:**
- Consumes: one real cron-job.org scheduled wake after the provider is enabled.
- Produces: final status `EXTERNAL_WAKE_RUNTIME_READY`, `EXTERNAL_WAKE_RUNTIME_DEGRADED`, or `EXTERNAL_WAKE_RUNTIME_FAILED`, reported separately from `PRODUCTION_LIVENESS` and overall Phase 4 readiness.

- [ ] **Step 1: Observe the first natural `:12` or `:42` provider execution**

Capture:

```text
provider planned time
provider actual execution time
provider HTTP status
GitHub Actions run ID
GitHub run created/started time
GitHub head SHA
```

Expected: provider receives HTTP 204 and a GitHub Actions run appears with `event=workflow_dispatch` on `main`.

- [ ] **Step 2: Inspect the GitHub job log**

Confirm the `Run external dispatch wake` step executed and the workflow source still resolves to:

```bash
python hardening_runner.py "dispatch"
```

Classify the dispatcher result:

```text
safe no-op/skipped because no slot is due -> healthy
one legitimate claimed/published due slot -> healthy
multiple catch-up publications in one wake -> failed safety property
runtime exception/job failure -> failed
```

- [ ] **Step 3: Verify no duplicate or catch-up flood**

Inspect the publication ledger/readiness evidence around the wake. There must be no duplicate publication of the same planned slot and no arbitrary burst of old slots. At most one due slot may be claimed by the wake under the existing dispatcher contract.

- [ ] **Step 4: Run production readiness read-only**

Use the existing production readiness path without schema/data mutation and capture the `liveness` line plus overall Phase 4 readiness.

Report statuses independently:

```text
EXTERNAL_WAKE_RUNTIME_READY
  real scheduled external wake delivered, workflow_dispatch run executed safely, no duplicate/flood

EXTERNAL_WAKE_RUNTIME_DEGRADED
  provider delivery or GitHub workflow start is delayed/pending but no actual receiver/dispatch failure is observed

EXTERNAL_WAKE_RUNTIME_FAILED
  provider configuration/auth fails persistently, workflow job fails, or duplicate/catch-up safety property is violated

PRODUCTION_LIVENESS_READY / DEGRADED / FAILED
  derived from the production readiness liveness evidence, not from repository CI
```

Do not relabel unrelated `strategy_versions` or learning-maturity degradations as a dispatch outage.

- [ ] **Step 5: Completion verification**

Phase 4N.4 is complete only when all are true:

```text
workflow merged to main
PR CI success
post-merge CI success
cron-job.org enabled at :12/:42 Asia/Ho_Chi_Minh
real provider execution returned HTTP 204
corresponding GitHub run event=workflow_dispatch on main
hardening_runner.py dispatch executed
result was safe no-op or one legitimate publish
no duplicate/catch-up flood
production liveness remains READY
external provider holds no Facebook/Turso application secrets
```

If any item lacks evidence, report the phase as pending/degraded rather than complete.
