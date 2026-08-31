# Style Experiment Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically keep, promote, or retire controlled style experiments from real mature Facebook metrics and make promoted variants eligible for production exploitation.

**Architecture:** Add a focused lifecycle evaluator that compares one pending experiment against its parent/control using the existing learning observations and recency aggregation. Extend feedback learning so experiment exposures also produce a strategy stat under the experiment token, then wire lifecycle review ahead of weekly experiment generation in the existing hardened action.

**Tech Stack:** Python 3.11, unittest, Turso/libSQL, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-31-style-experiment-lifecycle-design.md`

## Global Constraints

- Deterministic Python owns lifecycle decisions; Gemini cannot promote/retire.
- Minimum 5 mature final experiment samples and 5 mature parent/control samples.
- Reuse the existing 14-day learning window and recency weighting.
- Keep existing 80/20 exploit/explore behavior and mandatory quality gate.
- Preserve hardened error semantics: dependency/business failure must fail GitHub Actions.
- No destructive schema change and no new external dependency.

---

### Task 1: Lifecycle evaluator

**Files:**
- Create: `app/style_experiment_lifecycle.py`
- Create: `tests/test_style_experiment_lifecycle.py`

**Interfaces:**
- Consumes: `load_learning_observations(execute_fn, now=...)`, `list_active_styles(execute_fn, dimension)`, `set_style_status(execute_fn, style_id, status, changed_at=...)`, `aggregate_dimension(samples, now)`.
- Produces: `review_pending_experiment(execute_fn, now=None) -> LifecycleResult`.

- [x] **Step 1: Write failing tests** for no pending experiment, insufficient experiment data, insufficient parent/control data, promotion, retirement, and inconclusive keep-explore.
- [x] **Step 2: Run focused tests and verify RED.**
  Run: `python -m unittest tests.test_style_experiment_lifecycle -v`
- [x] **Step 3: Implement minimal deterministic evaluator** using exact experiment-key matching and baseline/custom parent controls.
- [x] **Step 4: Run focused tests and verify GREEN.**
- [x] **Step 5: Commit.**

### Task 2: Project experiment exposures into strategy stats

**Files:**
- Modify: `app/feedback_loop.py`
- Modify: `tests/test_feedback_loop.py`
- Create: `tests/test_feedback_experiment_projection.py`

**Interfaces:**
- Consumes: `LearningObservation.style_experiment_key` and `style_registry` lifecycle state.
- Produces: normal `StrategyStat` rows where promoted/custom experiment tokens can become mature `hook_type`, `style_type`, or `cta_type` values.

- [x] **Step 1: Write failing tests** proving an experiment exposure contributes both to its observed classifier stat and to exactly one experiment-token strategy stat, while malformed/unknown experiment keys are ignored.
- [x] **Step 2: Run focused tests and verify RED.**
- [x] **Step 3: Implement projection helpers** mapping `hook -> hook_type`, `tone -> style_type`, `cta -> cta_type`, include projected values/samples in regular-dimension refresh, keep pending experiments at zero weight, and fail closed if registry state cannot be read.
- [x] **Step 4: Run focused tests and verify GREEN.**
- [x] **Step 5: Commit.**

### Task 3: Hardened weekly lifecycle orchestration

**Files:**
- Modify: `hardening_runner.py`
- Modify: `tests/test_style_evolution_wiring.py`

**Interfaces:**
- Consumes: `review_pending_experiment(...)` and existing `generate_next_experiment(...)`.
- Produces: weekly `style_evolve` behavior that reviews first, blocks replacement while evidence is insufficient/inconclusive, and may generate one new experiment only after no pending experiment or a decisive transition.

- [x] **Step 1: Write failing tests** for keep-explore stopping generation, decisive promote/retire permitting one new generation, no-pending generation, and lifecycle dependency failure propagating as failure.
- [x] **Step 2: Run focused tests and verify RED.**
- [x] **Step 3: Implement the minimal orchestration adapter** while preserving JobOutcome success/skipped/error semantics.
- [x] **Step 4: Run focused tests and verify GREEN.**
- [x] **Step 5: Commit.**

### Task 4: Controlled attribution hardening

**Files:**
- Modify: `app/style_steering.py`
- Modify: `app/style_experiment_lifecycle.py`
- Modify: `tests/test_style_experiment_steering.py`
- Modify: `tests/test_style_experiment_lifecycle.py`

- [x] **Step 1: Add failing regression tests** proving promoted custom styles keep treatment attribution, child experiments can compare against a promoted custom parent, pending experiments use non-custom controls, and one post cannot carry multiple custom treatments.
- [x] **Step 2: Verify RED is limited to the new behavior.**
- [x] **Step 3: Preserve treatment keys in exploit, use promoted-parent exposures as lifecycle controls, and constrain each post to one custom treatment with baseline controls elsewhere.**
- [x] **Step 4: Verify GREEN and preserve the original exploit/explore contract.**
- [x] **Step 5: Commit.**

### Task 5: Full verification and PR

**Files:** no production changes expected.

- [x] **Step 1: Run full unit suite.**
  Run: `python -m unittest discover -s tests -v`
- [x] **Step 2: Compile all Python sources.**
  Run: `python -m compileall -q .`
- [x] **Step 3: Confirm branch diff is limited to 4K2B scope.**
- [x] **Step 4: Open PR against `main` and require green CI before merge.**

Latest verified code run before documentation closeout: CI #286, 292 unit tests plus compile gate, all passing.
