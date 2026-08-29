# Phase 4B — Quality Gate and Style Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate every adaptive draft on measurable quality and create a durable hook/style/CTA registry that supports controlled 80/20 experimentation.

**Architecture:** Build a pure scoring module around deterministic penalties plus bounded Gemini rubric output, then add a small style registry stored in Turso. Integrate through a reusable `prepare_publishable_candidate()` service rather than rewriting legacy job orchestration in-place.

**Tech Stack:** Python 3, JSON, dataclasses, Turso/libSQL, existing Gemini callable, `unittest`.

**Spec:** `docs/superpowers/specs/2026-08-29-adaptive-content-intelligence-design.md`

## Global Constraints

- Requires Phase 4A merged first.
- Publish threshold: `>=75`; rewrite: `65–74`; reject/change candidate: `<65`.
- Maximum rewrite attempts: 2.
- Quality overrides volume; a weak candidate may be skipped.
- New style variants start exploratory and do not directly change system weights.
- Gemini may generate/assess content but may not mutate strategy state.
- Existing hardened publishing path remains the only production path.

---

## File Map

- Create `app/quality.py`: rubric, penalties, quality result parsing.
- Create `app/style_registry.py`: durable hooks/styles/CTA definitions and states.
- Create `app/content_pipeline.py`: duplicate + quality + rewrite orchestration.
- Modify `app/db.py`: add style registry schema.
- Modify `app/job_adapters.py`: expose primary Facebook post id from adapted publishes without changing success/failure semantics.
- Create `tests/test_quality.py`, `tests/test_style_registry.py`, `tests/test_content_pipeline.py`.
- Modify `tests/test_job_adapters.py` for post-id capture compatibility.

### Task 1: Define quality contracts and deterministic score composition

**Files:**
- Create: `app/quality.py`
- Test: `tests/test_quality.py`

**Interfaces:**
- Produces `QualityRubric`, `QualityDecision`, `combine_quality_score(rubric, penalties)`, `decision_for_score(score)`.

- [ ] **Step 1: Write failing tests**

```python
from app.quality import QualityRubric, combine_quality_score, decision_for_score


def test_weighted_quality_score():
    rubric = QualityRubric(80, 70, 90, 80, 80, 70)
    assert combine_quality_score(rubric, []) == 79.0


def test_thresholds():
    assert decision_for_score(75).action == "publish"
    assert decision_for_score(70).action == "rewrite"
    assert decision_for_score(64.9).action == "reject"
```

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_quality -v`

Expected: import failure.

- [ ] **Step 3: Implement weighted scoring**

Use exact weights from the spec: novelty 25%, hook 20%, usefulness 20%, readability 15%, tone 10%, CTA 10%. Clamp component values to 0–100 and final score to 0–100. `QualityDecision` contains `score: float`, `action: str`, `reasons: tuple[str, ...]`.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_quality -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/quality.py tests/test_quality.py
git commit -m "feat: add deterministic quality scoring"
```

### Task 2: Add Gemini rubric parsing and explicit penalties

**Files:**
- Modify: `app/quality.py`
- Modify: `tests/test_quality.py`

**Interfaces:**
- Produces `assess_draft(candidate, recent, gemini_fn) -> QualityDecision`.

- [ ] **Step 1: Write failing tests**

Fake Gemini returns JSON with six rubric fields plus booleans `excessive_clickbait`, `repetitive_cta`, `hook_too_similar`, and string `reason`. Assert penalties: semantic duplicate −40, repeated hook −20, clickbait −20, repeated CTA −10, format-length violation −10. Assert malformed Gemini output returns a conservative `rewrite` decision, not a false publish.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_quality -v`

Expected: FAIL because `assess_draft` is missing.

- [ ] **Step 3: Implement bounded assessment**

Prompt requires JSON only. Cap recent examples to 12. Parse fenced JSON. A parser/API failure returns `QualityDecision(score=65.0, action="rewrite", reasons=("quality_assessment_unavailable",))` so the pipeline may retry once rather than publish blindly.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_quality -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/quality.py tests/test_quality.py
git commit -m "feat: add bounded Gemini quality rubric"
```

### Task 3: Add durable style registry

**Files:**
- Modify: `app/db.py`
- Create: `app/style_registry.py`
- Create: `tests/test_style_registry.py`
- Modify: `tests/test_db.py`

**Interfaces:**
- Produces `StyleVariant`, `ensure_seed_styles(execute_fn)`, `list_active_styles(execute_fn, dimension)`, `register_experiment(execute_fn, dimension, value, parent_value)`, `set_style_status(execute_fn, id, status)`.

- [ ] **Step 1: Write failing tests**

Assert schema has `style_registry(id, dimension, value, parent_value, status, created_at, promoted_at, retired_at)`. Seed hooks include `question`, `number`, `surprising_fact`, `direct_statement`, `contrast`, `curiosity`; tones include `concise_news`, `conversational`, `witty`, `explanatory`, `reflective`; CTA include `opinion_question`, `choose_side`, `experience_share`, `save_for_later`, `no_cta`.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_style_registry tests.test_db -v`

Expected: FAIL for missing registry.

- [ ] **Step 3: Implement schema/repository**

Use `INSERT OR IGNORE` with a unique constraint on `(dimension, value)`. Valid statuses are `baseline`, `explore`, `active`, `retired`; reject unknown status with `ValueError` before SQL.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_style_registry tests.test_db -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/db.py app/style_registry.py tests/test_db.py tests/test_style_registry.py
git commit -m "feat: add adaptive style registry"
```

### Task 4: Build candidate preparation pipeline

**Files:**
- Create: `app/content_pipeline.py`
- Create: `tests/test_content_pipeline.py`

**Interfaces:**
- Consumes 4A `check_local_duplicate`, `check_semantic_duplicate`, repository helpers, and 4B `assess_draft`.
- Produces `prepare_publishable_candidate(candidate, recent, gemini_fn, rewrite_fn, *, max_rewrites=2) -> PipelineResult`.

- [ ] **Step 1: Write failing pipeline tests**

Cover: exact duplicate rejects without Gemini; lexical duplicate rejects without quality call; semantic duplicate rejects; quality 81 publishes first draft; quality 70 calls rewrite and then publishes rewritten draft; two rewrites still below 75 returns `skipped_low_quality`; quality <65 switches/rejects rather than rewrites the same draft forever.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_content_pipeline -v`

Expected: import failure.

- [ ] **Step 3: Implement pipeline state machine**

`PipelineResult` fields: `status`, `candidate`, `quality_score`, `duplicate_score`, `detail`, `rewrite_count`. Never call `rewrite_fn` more than 2 times. Never publish from this module; it only returns a validated candidate to the existing publishing adapter.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_content_pipeline -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/content_pipeline.py tests/test_content_pipeline.py
git commit -m "feat: add pre-publish content pipeline"
```

### Task 5: Preserve Facebook publish outcome while exposing primary post id

**Files:**
- Modify: `app/job_adapters.py`
- Modify: `tests/test_job_adapters.py`

**Interfaces:**
- Produces optional callback on successful primary publish: `on_published(endpoint: str, payload: dict) -> None`.
- Default behavior remains unchanged when callback is omitted.

- [ ] **Step 1: Write failing compatibility tests**

Call `adapt_publish_job(..., on_published=spy)`; fake primary API returns `{"id": "123_456"}`; assert spy receives endpoint/payload once. Assert follow-up comments do not trigger callback. Existing tests without callback must remain unchanged.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_job_adapters -v`

Expected: unexpected keyword argument `on_published`.

- [ ] **Step 3: Implement optional callback**

Extend signature with keyword-only `on_published=None`. Invoke only after `_facebook_publish_succeeded` on a primary endpoint. Callback failures must raise because content ledger consistency is part of the primary adaptive flow once wired; do not swallow them silently.

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
git add app/job_adapters.py tests/test_job_adapters.py
git commit -m "feat: expose successful Facebook publish metadata"
```

## 4B Acceptance Gate

Required evidence: deterministic quality threshold behavior; max two rewrites; malformed quality assessment cannot false-green to publish; style registry is additive/idempotent; existing publish adapter behavior stays compatible; no production schedule change yet.
