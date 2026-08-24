# Video AutoBot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Pexels-based Facebook video bot with Turso deduplication and four daily GitHub Actions runs.

**Architecture:** A new `autobotvideo.py` owns video discovery/download/upload and persists posted Pexels IDs in Turso. Existing text/image jobs stay in `runner.py`; the workflow routes `video` to the new module and all other actions to `runner.py`.

**Tech Stack:** Python 3.11, requests, google-genai, libsql/Turso, GitHub Actions, Facebook Graph API, Pexels Video API.

**Spec:** `docs/superpowers/specs/2026-08-24-video-autobot-design.md`

## Global Constraints

- Do not use YouTube downloads, cookies, proxy, `yt_dlp`, or transformations intended to evade platform detection.
- Use existing repository secrets plus `PEXELS_API_KEY`.
- Video duration must be 5–90 seconds.
- Schedule video posts at 09:30, 12:45, 17:30, and 21:00 Vietnam time.
- Fail the workflow on operational posting failures.

---

### Task 1: Add video module

**Files:**
- Create: `autobotvideo.py`

**Interfaces:**
- Consumes: environment variables `PEXELS_API_KEY`, `GEMINI_API_KEYS`, `FB_ACCESS_TOKEN`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`.
- Produces: CLI `python autobotvideo.py run` and `python autobotvideo.py dry-run`.

- [ ] Implement environment validation and Turso `posted_videos` persistence.
- [ ] Implement Pexels video search, duration filtering, rendition selection, and download.
- [ ] Implement Gemini caption generation with Pexels attribution.
- [ ] Implement Facebook binary upload, Telegram notification, cleanup, and hard failure on errors.
- [ ] Verify with `python -m py_compile autobotvideo.py`.

### Task 2: Update dependencies and workflow

**Files:**
- Modify: `requirements.txt`
- Modify: `.github/workflows/facebook-autobot.yml`

**Interfaces:**
- Consumes: `python autobotvideo.py run`.
- Produces: manual `video` action and four scheduled video runs.

- [ ] Ensure requirements contain only libraries needed by both existing bot and video bot.
- [ ] Add `video` to manual choices.
- [ ] Add UTC schedules for 09:30, 12:45, 17:30, 21:00 Vietnam time.
- [ ] Map those schedules to `video`.
- [ ] Add `PEXELS_API_KEY` to workflow env.
- [ ] Route selected `video` action to `autobotvideo.py`; route all others to `runner.py`.

### Task 3: Verify repository state

**Files:**
- Read: `autobotvideo.py`
- Read: `requirements.txt`
- Read: `.github/workflows/facebook-autobot.yml`

- [ ] Confirm Python syntax.
- [ ] Confirm four video schedules and no schedule collision mapping errors.
- [ ] Confirm existing actions remain available.
- [ ] Record final commit SHAs for user handoff.
