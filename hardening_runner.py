import os
import sys
from datetime import datetime, timezone
from typing import Callable

import autobot
import autobotvideo
import runner
from app import db, notifications
from app.http import secure_session_from
from app.job_adapters import adapt_publish_job, adapt_reply_job
from app.job_contract import JobOutcome, run_job

VALID_ACTIONS = {
    "post",
    "reply",
    "finance",
    "philosophy",
    "summary",
    "veo",
    "recipe",
    "fun",
    "video",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def github_run_url() -> str | None:
    server = os.getenv("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    run_id = os.getenv("GITHUB_RUN_ID", "").strip()
    if not repository or not run_id:
        return None
    return f"{server}/{repository}/actions/runs/{run_id}"


def make_run_key(action: str, started_at: str) -> str:
    run_id = os.getenv("GITHUB_RUN_ID", "").strip()
    attempt = os.getenv("GITHUB_RUN_ATTEMPT", "1").strip() or "1"
    if run_id:
        return f"{run_id}-{attempt}-{action}"
    safe_time = started_at.replace(":", "").replace("+", "-")
    return f"local-{action}-{safe_time}"


def resolve_jobs() -> dict[str, Callable[[], object]]:
    """Resolve jobs through shared DB, verified HTTP and legacy outcome adapters."""
    autobot.execute_db = db.execute
    autobotvideo.db_execute = db.execute
    autobot.http = secure_session_from(autobot.http)
    autobotvideo.http = secure_session_from(autobotvideo.http)

    return {
        "post": adapt_publish_job(
            autobot.single_post_job,
            autobot,
            lambda endpoint: endpoint == "me/feed",
            allow_skip=True,
        ),
        "reply": adapt_reply_job(autobot.auto_reply_job, autobot),
        "finance": adapt_publish_job(
            autobot.financial_post_job,
            autobot,
            lambda endpoint: endpoint == "me/feed",
            allow_skip=False,
        ),
        "philosophy": adapt_publish_job(
            autobot.philosophy_post_job,
            autobot,
            lambda endpoint: endpoint == "me/feed",
            allow_skip=False,
        ),
        "summary": adapt_publish_job(
            autobot.daily_summary_job,
            autobot,
            lambda endpoint: endpoint.endswith("/photos"),
            allow_skip=True,
        ),
        "veo": autobot.veo_prompt_job,
        "recipe": runner.recipe_job,
        "fun": runner.fun_job,
        "video": lambda: autobotvideo.video_post_job(dry_run=False),
    }


def run_action(action: str, jobs: dict[str, Callable[[], object]] | None = None) -> JobOutcome:
    if action not in VALID_ACTIONS:
        raise ValueError(f"Action không hợp lệ: {action}")

    if jobs is None:
        jobs = resolve_jobs()
    if action not in jobs:
        raise ValueError(f"Action không hợp lệ: {action}")

    started_at = utc_now_iso()
    run_key = make_run_key(action, started_at)

    db.ensure_schema()
    db.record_job(run_key, action, "started", started_at, None, "")

    def recorder(status: str, detail: str = "") -> None:
        finished_at = utc_now_iso()
        db.record_job(run_key, action, status, started_at, finished_at, detail[:1000])

    def notifier(failed_action: str, error: Exception) -> None:
        notifications.send_failure(failed_action, error, github_run_url())

    return run_job(action, jobs[action], recorder, notifier)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(
            "Cách dùng: python hardening_runner.py "
            "<post|reply|finance|philosophy|summary|veo|recipe|fun|video>"
        )
    run_action(sys.argv[1])


if __name__ == "__main__":
    main()
