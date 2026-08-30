# Adaptive Creative Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make production adaptive posts select, apply, and persist measurable hook/style/CTA variants.

**Architecture:** Add one deterministic creative selector over the existing style registry and strategy stats. The dispatcher places its result in publication context; generators consume only a fixed prompt suffix, and the publication ledger records the exact selected values.

**Tech Stack:** Python 3.11, stdlib `random/hashlib/contextvars`, Turso/libSQL, existing Gemini and Facebook adapters.

**Spec:** `docs/superpowers/specs/2026-08-30-adaptive-creative-strategy-design.md`

## Global Constraints
- No direct writes to `main`; PR + required `test` check.
- TDD RED before implementation changes.
- 80% exploit / 20% explore from adaptive config; deterministic per dispatcher run/strategy version.
- Missing learning data falls back to registered baseline values.
- Retired variants are never selectable.
- Fixed prompt mappings only; never invent facts for a hook.
- Manual publishes remain isolated from adaptive learning.

---

### Task 1: Deterministic creative selector

**Files:**
- Create: `app/creative_strategy.py`
- Test: `tests/test_creative_strategy.py`

**Interfaces:**
- Produces `CreativeProfile(hook_type, style_type, cta_type)`.
- Produces `select_creative_profile(execute_fn, *, run_key, category, strategy_version) -> CreativeProfile`.
- Produces `creative_prompt_suffix(profile) -> str`.

- [ ] Write SQLite-backed RED tests proving seed fallback, deterministic selection, retired-value exclusion, and fixed safe prompt mappings.
- [ ] Run `python -m unittest tests.test_creative_strategy -v` and confirm RED.
- [ ] Implement selector using `style_registry`, `load_config`, `load_stats`, and `select_strategy`.
- [ ] Re-run targeted tests and confirm GREEN.

### Task 2: Dispatcher/context propagation

**Files:**
- Modify: `app/publication_context.py`
- Modify: `app/dispatcher.py`
- Test: `tests/test_dispatcher.py`

**Interfaces:**
- `PublicationContext` gains `hook_type`, `style_type`, `cta_type` defaults.
- Dispatcher selects exactly one profile after a slot is claimed and before business execution.

- [ ] Add RED tests that the business job sees the selected creative fields and selector failure prevents publication.
- [ ] Run targeted dispatcher tests and confirm RED.
- [ ] Implement minimal context/dispatcher wiring.
- [ ] Re-run targeted tests and confirm GREEN.

### Task 3: Production prompt consumption

**Files:**
- Modify: `autobot.py`
- Modify: `runner.py`
- Modify: `autobotvideo.py`
- Test: `tests/test_creative_prompt_wiring.py`

**Interfaces:**
- Generators call a shared helper that reads current publication context and appends fixed hook/style/CTA guidance.

- [ ] Add RED source/behavior tests for post, finance, philosophy, fun, recipe and video caption generation.
- [ ] Run targeted tests and confirm RED.
- [ ] Append creative guidance while preserving each job's factual/content constraints.
- [ ] Re-run targeted tests and confirm GREEN.

### Task 4: Canonical ledger metadata

**Files:**
- Modify: `app/publication_ledger.py`
- Test: `tests/test_publication_ledger.py`
- Test: `tests/test_production_ledger_wiring.py`

**Interfaces:**
- Adaptive confirmed publishes persist `hook_type/style_type/cta_type` from publication context into `ContentCandidate`.
- Manual/no-context publishes retain defaults.

- [ ] Add RED tests for exact metadata persistence and manual fallback.
- [ ] Run targeted tests and confirm RED.
- [ ] Implement minimal ledger mapping.
- [ ] Re-run targeted tests and confirm GREEN.

### Task 5: Full acceptance and PR

**Files:** no production additions unless acceptance exposes a defect.

- [ ] Run `python -m unittest discover -s tests -v` in CI.
- [ ] Run `python -m compileall -q .` in CI.
- [ ] Review PR diff and review threads.
- [ ] Merge only with the exact verified head SHA.
- [ ] Verify the push CI on the exact new `main` SHA before declaring Phase 4I complete.
