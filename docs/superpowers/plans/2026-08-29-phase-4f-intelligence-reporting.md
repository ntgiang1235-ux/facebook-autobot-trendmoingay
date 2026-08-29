# Phase 4F — Daily and Weekly Intelligence Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send concise daily and deeper weekly Telegram reports that explain both performance and the strategy changes the bot made.

**Architecture:** Build report data from Turso through pure aggregation helpers, render deterministic text, then deliver through the existing shared Telegram notification layer. Reporting must remain observational: it may describe strategy changes but must not itself mutate strategy state.

**Tech Stack:** Python 3, Turso/libSQL, existing `app.notifications.send_message`, `unittest`, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-29-adaptive-content-intelligence-design.md`

## Global Constraints

- Requires 4C–4E merged.
- Daily report is concise; weekly report is deeper and includes 7-day results, 14-day learning state, and prior-week comparison when enough data exists.
- Reports must explicitly state what strategy changed.
- Metric capability degradation must be visible when it materially affects interpretation.
- Telegram failure remains non-masking for unrelated original business failures, consistent with current hardening behavior.
- Reporting jobs never alter category weights, daily plans, or strategy versions.

---

## File Map

- Create `app/reporting.py`: report models, aggregation, renderers.
- Create `reporting_runner.py`: daily/weekly job entry points.
- Modify `hardening_runner.py`: add `daily_report` and `weekly_report` actions.
- Modify `app/notifications.py`: chunk-safe intelligence sender.
- Modify `.github/workflows/facebook-autobot.yml`: daily/weekly report schedules.
- Create `tests/test_reporting.py`, `tests/test_reporting_runner.py`.
- Modify `tests/test_notifications.py`, `tests/test_hardening_runner.py`, `tests/test_workflows.py`.

### Task 1: Build daily report dataset and renderer

**Files:**
- Create: `app/reporting.py`
- Test: `tests/test_reporting.py`

**Interfaces:**
- Produces `DailyReportData`, `build_daily_report_data(execute_fn, report_date)`, `render_daily_report(data) -> str`.

- [ ] **Step 1: Write failing daily report tests**

Construct repository rows for 11 published posts, one `skipped_low_quality`, top finance score 88, weak recipe score 43, one category suspension, one weight increase, and one metric capability warning. Assert rendered text includes published count, quality skips, current average score, strongest/weakest item, and an `AI decisions` section naming the actual changes.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_reporting.DailyReportingTests -v`

Expected: import failure.

- [ ] **Step 3: Implement daily aggregation/rendering**

Keep the renderer deterministic around this structure:

```python
def render_daily_report(data: DailyReportData) -> str:
    average = "pending" if data.average_score is None else f"{data.average_score:.1f}"
    lines = [
        "TREND MỖI NGÀY — Daily AI Report",
        f"Published: {data.published} | Quality skips: {data.quality_skips}",
        f"Average score: {average}",
        f"Top: {data.top_label}",
        f"Weak: {data.weak_label}",
        "AI decisions:",
        *[f"• {item}" for item in data.decisions],
    ]
    if data.warnings:
        lines.append("Warnings: " + "; ".join(data.warnings))
    return "\n".join(lines)
```

When no mature score exists, render `pending`, never `0`.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_reporting.DailyReportingTests -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/reporting.py tests/test_reporting.py
git commit -m "feat: render daily content intelligence report"
```

### Task 2: Build weekly intelligence report

**Files:**
- Modify: `app/reporting.py`
- Modify: `tests/test_reporting.py`

**Interfaces:**
- Produces `WeeklyReportData`, `build_weekly_report_data(execute_fn, week_end)`, `render_weekly_report(data) -> str`.

- [ ] **Step 1: Write failing weekly tests**

Provide current-week and previous-week scores and dimension stats. Assert output includes overall score trend, best/worst categories, best hook/style/CTA/format/time window, exploration promoted/rejected counts, suspended/reactivated categories, and major strategy weight movements. When previous week has fewer than five mature posts, assert renderer says comparison is unavailable instead of inventing a percentage.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_reporting.WeeklyReportingTests -v`

Expected: FAIL for missing weekly functions.

- [ ] **Step 3: Implement weekly renderer**

```python
def _top(items, limit=3):
    return sorted(items, key=lambda item: (-item.score, item.name))[:limit]


def render_weekly_report(data: WeeklyReportData) -> str:
    trend = (
        "comparison unavailable"
        if data.previous_average is None
        else f"{((data.current_average / data.previous_average) - 1) * 100:+.1f}%"
    )
    lines = [
        "TREND MỖI NGÀY — Weekly Content Intelligence",
        f"Overall score: {data.current_average:.1f} ({trend})",
        f"Best category: {data.best_category}",
        f"Weakest category: {data.weakest_category}",
        f"Best hook/style: {data.best_hook} / {data.best_style}",
        f"Best time: {data.best_time}",
        f"Exploration: {data.promoted_experiments} promoted, {data.rejected_experiments} rejected",
    ]
    lines.extend(f"• {movement}" for movement in data.strategy_movements)
    return "\n".join(lines)
```

Only compute percentage trend when prior-week maturity is adequate and previous average is positive.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_reporting.WeeklyReportingTests -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/reporting.py tests/test_reporting.py
git commit -m "feat: render weekly content intelligence report"
```

### Task 3: Add safe Telegram delivery for intelligence reports

**Files:**
- Modify: `app/notifications.py`
- Modify: `tests/test_notifications.py`

**Interfaces:**
- Produces `send_intelligence_report(title: str, body: str) -> bool`.

- [ ] **Step 1: Write failing delivery tests**

Assert HTML special characters are escaped consistently with existing notifications, report length above Telegram message limit is split on section/newline boundaries into bounded chunks, chunks are sent in order, and a failed chunk returns `False` without raising a second exception that masks the caller's original error path.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_notifications -v`

Expected: FAIL for missing helper.

- [ ] **Step 3: Implement chunk-safe sender**

```python
def _chunks(text: str, max_chars: int = 3500) -> list[str]:
    lines = text.splitlines()
    chunks, current = [], ""
    for line in lines:
        candidate = f"{current}\n{line}".strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def send_intelligence_report(title: str, body: str) -> bool:
    return all(send_message(chunk) is True for chunk in _chunks(f"{title}\n{body}"))
```

Preserve the existing secure Telegram session and existing escaping behavior inside `send_message`/notification helpers; tests decide whether title/body should be escaped before chunking based on current implementation.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_notifications -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/notifications.py tests/test_notifications.py
git commit -m "feat: send chunked intelligence reports"
```

### Task 4: Add hardened reporting jobs

**Files:**
- Create: `reporting_runner.py`
- Modify: `hardening_runner.py`
- Create: `tests/test_reporting_runner.py`
- Modify: `tests/test_hardening_runner.py`

**Interfaces:**
- Produces `run_daily_report()`, `run_weekly_report()`, hardened actions `daily_report`, `weekly_report`.

- [ ] **Step 1: Write failing orchestration tests**

Assert daily action builds then sends exactly one logical report; weekly action does the same; missing Telegram config produces a visible failed job; no report action calls planner or strategy repository mutation functions.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_reporting_runner tests.test_hardening_runner -v`

Expected: FAIL because actions are missing.

- [ ] **Step 3: Implement reporting runner/wiring**

```python
def run_daily_report():
    data = reporting.build_daily_report_data(db.execute, date.today())
    body = reporting.render_daily_report(data)
    if not notifications.send_intelligence_report("Daily AI Report", body):
        raise RuntimeError("daily intelligence report delivery failed")


def run_weekly_report():
    data = reporting.build_weekly_report_data(db.execute, date.today())
    body = reporting.render_weekly_report(data)
    if not notifications.send_intelligence_report("Weekly Content Intelligence", body):
        raise RuntimeError("weekly intelligence report delivery failed")
```

Wire those through `hardening_runner.resolve_jobs()` so `run_job` remains responsible for job outcome recording.

- [ ] **Step 4: Verify GREEN**

Run: `python -m unittest tests.test_reporting_runner tests.test_hardening_runner -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add reporting_runner.py hardening_runner.py tests/test_reporting_runner.py tests/test_hardening_runner.py
git commit -m "feat: add hardened intelligence report jobs"
```

### Task 5: Schedule daily and weekly reports

**Files:**
- Modify: `.github/workflows/facebook-autobot.yml`
- Modify: `tests/test_workflows.py`

**Interfaces:**
- Daily report cron: `15 15 * * *` UTC = 22:15 Vietnam.
- Weekly report cron: `30 15 * * 0` UTC = 22:30 Sunday Vietnam.

- [ ] **Step 1: Write failing workflow tests**

Extend the existing `WorkflowTests` in `tests/test_workflows.py`:

```python
def test_intelligence_report_schedules(self):
    prod = (ROOT / ".github/workflows/facebook-autobot.yml").read_text(encoding="utf-8")
    self.assertIn('cron: "15 15 * * *"', prod)
    self.assertIn('cron: "30 15 * * 0"', prod)
    self.assertIn('ACTION="daily_report"', prod)
    self.assertIn('ACTION="weekly_report"', prod)
```

Also assert those mappings do not call `dispatcher_runner.py` or a content publish action for the same cron branch.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_workflows -v`

Expected: FAIL because report schedules are absent.

- [ ] **Step 3: Add workflow mappings**

Add the two cron entries and map them explicitly to `daily_report` and `weekly_report`; preserve manual action support. Reuse Turso and Telegram secrets. Do not add a Facebook publish call in either branch.

- [ ] **Step 4: Full verification**

```bash
python -m unittest tests.test_workflows -v
python -m unittest discover -s tests -v
python -m compileall -q .
git diff --check
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/facebook-autobot.yml tests/test_workflows.py
git commit -m "ci: schedule content intelligence reports"
```

## 4F Acceptance Gate

Evidence must show daily and weekly renderers from fixture data, no fake zeros for pending metrics, visible adaptive decisions, capability warnings, chunk-safe Telegram delivery, read-only reporting behavior, and correct Vietnam/UTC schedule mapping.
