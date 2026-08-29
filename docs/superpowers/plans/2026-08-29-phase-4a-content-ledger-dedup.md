# Phase 4A — Content Ledger and Anti-Duplicate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a durable content ledger and layered exact/lexical/semantic duplicate detection without changing production scheduling.

**Architecture:** Introduce focused data/repository/dedup modules under `app/`. Extend `app.db.ensure_schema()` idempotently, then expose pure duplicate checks that can be called by later generation/quality phases. Keep existing `posted_news` protection intact during this phase so rollout is additive and reversible.

**Tech Stack:** Python 3, stdlib `dataclasses`, `hashlib`, `re`, `difflib`, JSON, Turso/libSQL, existing Gemini callable, `unittest`.

**Spec:** `docs/superpowers/specs/2026-08-29-adaptive-content-intelligence-design.md`

## Global Constraints

- Learning window remains 14 days; anti-repeat windows are category-specific and independent of that window.
- No vector database, Redis, VPS, always-on worker, or new paid service.
- Existing hardened runner, DB retry, secure HTTP, Telegram notifications, `job_runs`, and stale-run protections must remain intact.
- Exact-link protection for existing news posts must not be removed in this phase.
- Semantic duplicate checks must be bounded and only run after cheaper checks.
- Production scheduling is unchanged in 4A.

---

## File Map

- Create `app/content_models.py`: immutable content/duplicate dataclasses shared by later phases.
- Create `app/content_repository.py`: CRUD/query helpers for content ledger.
- Create `app/dedup.py`: normalization, lexical similarity, category windows, semantic decision parsing.
- Modify `app/db.py`: create/migrate `content_posts` and indexes idempotently.
- Create `tests/test_content_repository.py`.
- Create `tests/test_dedup.py`.
- Modify `tests/test_db.py` to assert the new schema is safe on repeated calls.

### Task 1: Define content ledger models

**Files:**
- Create: `app/content_models.py`
- Test: `tests/test_dedup.py`

**Interfaces:**
- Produces: `ContentCandidate`, `RecentContent`, `DuplicateDecision` dataclasses.
- Later tasks consume these exact names.

- [ ] **Step 1: Write the failing model test**

```python
from app.content_models import ContentCandidate


def test_content_candidate_keeps_strategy_metadata():
    item = ContentCandidate(
        category="finance",
        topic_key="gold-price",
        topic_text="Giá vàng tăng",
        content_text="Nội dung",
        source_url="https://example.test/a",
        source_title="Giá vàng",
        hook_type="number",
        style_type="explanatory",
        cta_type="opinion_question",
        format_type="text",
    )
    assert item.category == "finance"
    assert item.topic_key == "gold-price"
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m unittest tests.test_dedup.ContentModelTests -v`

Expected: import failure because `app.content_models` does not exist.

- [ ] **Step 3: Implement the models**

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ContentCandidate:
    category: str
    topic_key: str
    topic_text: str
    content_text: str
    source_url: str | None = None
    source_title: str | None = None
    hook_type: str = "unknown"
    style_type: str = "unknown"
    cta_type: str = "none"
    format_type: str = "text"


@dataclass(frozen=True)
class RecentContent:
    id: int
    category: str
    topic_key: str
    topic_text: str
    content_text: str
    source_url: str | None
    published_at: str | None


@dataclass(frozen=True)
class DuplicateDecision:
    duplicate: bool
    score: float
    layer: str
    reason: str
```

- [ ] **Step 4: Run the focused test**

Run: `python -m unittest tests.test_dedup.ContentModelTests -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/content_models.py tests/test_dedup.py
git commit -m "feat: add content intelligence models"
```

### Task 2: Add idempotent `content_posts` schema

**Files:**
- Modify: `app/db.py`
- Modify: `tests/test_db.py`

**Interfaces:**
- Consumes: existing `execute()` and `ensure_schema()`.
- Produces: durable `content_posts` table with fields required by the approved spec.

- [ ] **Step 1: Add a failing schema test**

Use a fake `execute` recorder and assert `ensure_schema()` issues a `CREATE TABLE IF NOT EXISTS content_posts` statement containing at least `facebook_post_id`, `category`, `topic_key`, `content_text`, `hook_type`, `style_type`, `cta_type`, `format_type`, `strategy_mode`, `quality_score`, `duplicate_score`, `strategy_version`, `status`, and `detail`.

Also call `ensure_schema()` twice in the test and assert no destructive statement such as `DROP TABLE` occurs.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_db -v`

Expected: FAIL because `content_posts` is not created.

- [ ] **Step 3: Extend `ensure_schema()`**

Add this additive schema after `job_runs` setup:

```python
execute(
    """
    CREATE TABLE IF NOT EXISTS content_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_key TEXT,
        facebook_post_id TEXT,
        action TEXT,
        category TEXT NOT NULL,
        topic_key TEXT NOT NULL,
        topic_text TEXT NOT NULL,
        source_url TEXT,
        source_title TEXT,
        content_text TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        hook_type TEXT NOT NULL DEFAULT 'unknown',
        style_type TEXT NOT NULL DEFAULT 'unknown',
        cta_type TEXT NOT NULL DEFAULT 'none',
        format_type TEXT NOT NULL DEFAULT 'text',
        scheduled_for TEXT,
        published_at TEXT,
        strategy_mode TEXT NOT NULL DEFAULT 'baseline',
        quality_score REAL,
        duplicate_score REAL,
        strategy_version INTEGER,
        status TEXT NOT NULL,
        detail TEXT,
        created_at TEXT NOT NULL
    )
    """
)
execute("CREATE INDEX IF NOT EXISTS idx_content_posts_category_time ON content_posts(category, published_at)")
execute("CREATE INDEX IF NOT EXISTS idx_content_posts_topic_time ON content_posts(topic_key, published_at)")
execute("CREATE INDEX IF NOT EXISTS idx_content_posts_facebook_id ON content_posts(facebook_post_id)")
```

Do not delete or rewrite existing `posted_news` data.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_db -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat: add content ledger schema"
```

### Task 3: Implement repository write/read helpers

**Files:**
- Create: `app/content_repository.py`
- Create: `tests/test_content_repository.py`

**Interfaces:**
- Produces:
  - `content_hash(text: str) -> str`
  - `record_candidate(execute_fn, candidate, *, run_key=None, status="generated", **metadata) -> int | None`
  - `recent_content(execute_fn, category: str, since_iso: str, limit: int = 30) -> list[RecentContent]`
  - `mark_published(execute_fn, content_id: int, facebook_post_id: str, published_at: str) -> None`
  - `mark_rejected(execute_fn, content_id: int, detail: str, duplicate_score: float | None = None) -> None`

- [ ] **Step 1: Write failing repository tests**

Test that `content_hash("  Xin   chào ") == content_hash("xin chào")`; that `record_candidate` inserts normalized metadata; that `recent_content` maps tuples to `RecentContent`; and that `mark_published` only updates one row by id.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_content_repository -v`

Expected: import failure.

- [ ] **Step 3: Implement repository**

Normalize hashes with:

```python
def content_hash(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
```

All SQL must be parameterized. `recent_content()` must order newest-first and apply the supplied `since_iso` and `limit` rather than interpolating them into SQL.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_content_repository -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/content_repository.py tests/test_content_repository.py
git commit -m "feat: add content ledger repository"
```

### Task 4: Implement exact and lexical duplicate layers

**Files:**
- Create: `app/dedup.py`
- Modify: `tests/test_dedup.py`

**Interfaces:**
- Produces:
  - `normalize_text(text: str) -> str`
  - `lexical_similarity(a: str, b: str) -> float`
  - `anti_repeat_days(category: str) -> int`
  - `check_local_duplicate(candidate: ContentCandidate, recent: list[RecentContent], threshold: float = 0.80) -> DuplicateDecision | None`

- [ ] **Step 1: Write failing tests**

Cover Vietnamese punctuation/case normalization, same source URL => exact duplicate, same `topic_key` => exact semantic identity, clearly similar titles >= 0.80, unrelated titles below threshold, and category windows: news 7, finance 14, fun 14, recipe 30, philosophy 30, video 30.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_dedup -v`

Expected: FAIL for missing functions.

- [ ] **Step 3: Implement local checks**

Use `unicodedata.normalize("NFKC", text)`, lowercase, punctuation-to-space regex, whitespace collapse, and `difflib.SequenceMatcher(None, normalized_a, normalized_b).ratio()`.

Exact checks run before lexical checks. Return `None` only when local layers are inconclusive.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_dedup -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/dedup.py tests/test_dedup.py
git commit -m "feat: add exact and lexical duplicate checks"
```

### Task 5: Add bounded semantic duplicate fallback

**Files:**
- Modify: `app/dedup.py`
- Modify: `tests/test_dedup.py`

**Interfaces:**
- Produces: `check_semantic_duplicate(candidate, recent, gemini_fn, limit: int = 20) -> DuplicateDecision`.

- [ ] **Step 1: Write failing semantic tests**

Use a fake Gemini function that captures the prompt and returns:

```json
{"duplicate": true, "similarity": 0.91, "reason": "same event"}
```

Assert only the first 20 recent records are sent, malformed JSON returns a safe non-duplicate decision with layer `semantic_unavailable`, and no exception is raised solely because the semantic helper cannot parse an answer.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_dedup -v`

Expected: FAIL for missing semantic helper.

- [ ] **Step 3: Implement semantic parser**

The prompt must explicitly require JSON only with keys `duplicate`, `similarity`, `reason`. Parse fenced JSON defensively. Clamp similarity to `[0.0, 1.0]`. A Gemini/parsing failure must return:

```python
DuplicateDecision(False, 0.0, "semantic_unavailable", "semantic check unavailable")
```

Do not treat API failure as proof that content is unique; later quality phases may choose to skip if policy requires it.

- [ ] **Step 4: Verify GREEN and full regression suite**

Run:

```bash
python -m unittest tests.test_dedup -v
python -m unittest discover -s tests -v
python -m compileall -q .
```

Expected: all tests PASS and compile succeeds.

- [ ] **Step 5: Commit**

```bash
git add app/dedup.py tests/test_dedup.py
git commit -m "feat: add semantic duplicate fallback"
```

## 4A Acceptance Gate

Before opening the 4A PR, verify:

```bash
python -m unittest discover -s tests -v
python -m compileall -q .
git diff --check
```

Required evidence: content schema is additive/idempotent; exact and lexical checks work without Gemini; semantic check is bounded to 20 recent items; no scheduler/workflow changes; existing production tests remain green.
