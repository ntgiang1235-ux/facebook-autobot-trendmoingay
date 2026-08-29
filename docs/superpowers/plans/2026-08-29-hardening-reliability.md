# Hardening & Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Làm cho mọi job báo trạng thái đúng, retry Turso ổn định, CI kiểm tra toàn repo, TLS an toàn, dependencies reproducible và có lịch sử job/Telegram failure alert.

**Architecture:** Giữ các job hiện tại nhưng đưa orchestration, database retry và notification vào các module nhỏ có test. Workflow gọi một entry point đáng tin cậy, ghi `job_runs`, phân biệt success/skipped/failed và re-raise lỗi thật.

**Tech Stack:** Python 3.11, unittest, requests, libsql/Turso, GitHub Actions, Facebook Graph API, Gemini API, Pexels API, Telegram Bot API.

**Spec:** `docs/superpowers/specs/2026-08-29-hardening-reliability-design.md`

## Global Constraints

- Không đổi tên GitHub Secrets hiện tại.
- Không đổi cron hiện tại.
- Không đổi Facebook Graph API version trong phase này.
- Không thêm VPS, Redis, Celery, Docker hoặc database mới.
- Không gọi API thật trong unit tests.
- Mọi lỗi nghiệp vụ thực phải tạo exit code non-zero; `skipped` không phải lỗi.

---

### Task 1: CI baseline and full test discovery

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `.github/workflows/facebook-autobot.yml`

**Interfaces:**
- Produces: CI chạy `python -m unittest discover -s tests -v` và `python -m compileall -q .`.

- [ ] **Step 1:** Tạo `ci.yml` trigger `push`/`pull_request`, checkout, Python 3.11, pip install, unittest discover, compileall.
- [ ] **Step 2:** Đổi workflow production từ test riêng `tests.test_db_retry` sang full discovery.
- [ ] **Step 3:** Push commit và xác nhận CI branch chạy.

### Task 2: Job outcome contract

**Files:**
- Create: `app/__init__.py`
- Create: `app/job_contract.py`
- Create: `tests/test_job_contract.py`

**Interfaces:**
- Produces: `JobOutcome(status: str, detail: str = "")`, `success()`, `skipped(detail)`, `run_job(action, job_fn, recorder, notifier)`.

- [ ] **Step 1:** Viết test: job return bình thường => success; `JobOutcome("skipped")` => skipped; exception => recorder failed, notifier gọi, exception được re-raise; recorder/notifier failure không che exception gốc.
- [ ] **Step 2:** Chạy test và xác nhận fail vì module chưa tồn tại.
- [ ] **Step 3:** Implement tối thiểu `app/job_contract.py`.
- [ ] **Step 4:** Chạy toàn bộ test.

### Task 3: Turso database module and job ledger

**Files:**
- Create: `app/db.py`
- Create: `tests/test_db.py`
- Modify: `db_retry.py` only if tests expose gap.

**Interfaces:**
- Produces: `execute(query, params=())`, `ensure_schema()`, `record_job(run_key, action, status, started_at, finished_at=None, detail="")`.
- Consumes: `db_retry.run_with_retry`.

- [ ] **Step 1:** Viết tests bằng fake connector cho retry delegation và SQL `job_runs` schema/recording.
- [ ] **Step 2:** Verify RED.
- [ ] **Step 3:** Implement `app/db.py` với connection per operation và retry.
- [ ] **Step 4:** Verify GREEN toàn suite.

### Task 4: Telegram notification module

**Files:**
- Create: `app/notifications.py`
- Create: `tests/test_notifications.py`

**Interfaces:**
- Produces: `send_failure(action, error, run_url=None)` và `send_message(message)`; notification failure chỉ log, không raise.

- [ ] **Step 1:** Test message format và behavior khi thiếu Telegram config.
- [ ] **Step 2:** Verify RED.
- [ ] **Step 3:** Implement dùng `requests.Session` timeout 15s.
- [ ] **Step 4:** Verify GREEN.

### Task 5: Reliable orchestrator without rewriting jobs

**Files:**
- Create: `hardening_runner.py`
- Create: `tests/test_hardening_runner.py`
- Modify: `.github/workflows/facebook-autobot.yml`

**Interfaces:**
- Consumes existing `runner` and `autobotvideo` jobs, `app.db`, `app.notifications`, `app.job_contract`.
- Produces CLI: `python hardening_runner.py <post|reply|finance|philosophy|summary|veo|recipe|fun|video>`.

- [ ] **Step 1:** Test action dispatch, invalid action, exception propagation, skipped detection for known no-content results.
- [ ] **Step 2:** Verify RED.
- [ ] **Step 3:** Implement dispatch mapping. Existing retry wrappers remain available but production workflow moves to new runner only after test pass.
- [ ] **Step 4:** Workflow uses `hardening_runner.py` for every action.
- [ ] **Step 5:** Verify full tests and compileall.

### Task 6: Remove insecure TLS bypass

**Files:**
- Modify: `autobot.py`
- Create: `tests/test_security_source.py`

**Interfaces:**
- Produces: source contains no `verify=False` or `urllib3.disable_warnings`.

- [ ] **Step 1:** Write source-scan test asserting banned patterns absent.
- [ ] **Step 2:** Verify RED against current source.
- [ ] **Step 3:** Remove `verify=False`, `urllib3.disable_warnings` and unused urllib3 import if no longer required.
- [ ] **Step 4:** Verify full tests and compileall.

### Task 7: Dependency reproducibility and update automation

**Files:**
- Modify: `requirements.txt`
- Create: `.github/dependabot.yml`

**Interfaces:**
- Produces: exact version pins for current dependencies and weekly Dependabot checks for pip and github-actions.

- [ ] **Step 1:** Resolve currently compatible versions from a successful GitHub Actions install/run or conservative current package versions.
- [ ] **Step 2:** Pin all direct dependencies exactly.
- [ ] **Step 3:** Add Dependabot weekly configuration.
- [ ] **Step 4:** Verify fresh CI install succeeds.

### Task 8: Production error propagation adapters

**Files:**
- Create: `app/job_adapters.py`
- Create: `tests/test_job_adapters.py`
- Modify: `hardening_runner.py`

**Interfaces:**
- Produces adapters for existing jobs that convert known silent failure returns into exceptions where observable without rewriting job internals.

- [ ] **Step 1:** Add tests for API failure signals that currently print-and-return where adapters can detect them.
- [ ] **Step 2:** Implement minimal adapters around final publish operations/return values exposed by existing job functions.
- [ ] **Step 3:** Where a legacy job provides no observable outcome, leave behavior unchanged and document as remaining debt rather than guessing.
- [ ] **Step 4:** Run full suite.

### Task 9: Final verification and integration

**Files:**
- Review all branch changes.

**Interfaces:**
- Produces: verified branch eligible to fast-forward `main`.

- [ ] **Step 1:** Confirm branch CI success on latest SHA.
- [ ] **Step 2:** Confirm workflow cron strings exactly match `main` pre-hardening schedule.
- [ ] **Step 3:** Confirm secret names unchanged.
- [ ] **Step 4:** Compare `main...codex/hardening-reliability` and review changed files.
- [ ] **Step 5:** Fast-forward `main` only if all checks pass.
- [ ] **Step 6:** Re-check production workflow run on the merged SHA when GitHub schedules the next action; do not manually change secrets/database.
