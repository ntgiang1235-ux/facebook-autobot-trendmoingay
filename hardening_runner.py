import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Callable

import autobot
import autobotvideo
import metrics_runner
import runner
from app import (
    adaptive_jobs,
    content_repository,
    db,
    dedup,
    dispatcher,
    feedback_loop,
    health,
    notifications,
    prepublish_guard,
    publication_ledger,
    reporting,
    scheduler,
)
from app.http import secure_session_from
from app.job_adapters import adapt_delivery_job, adapt_publish_job, adapt_reply_job
from app.job_contract import JobOutcome, run_job, skipped

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
    "health",
    "metrics",
    "learn",
    "planner",
    "dispatch",
    "report_daily",
    "report_weekly",
}
ADAPTIVE_CONTENT_ACTIONS = (
    "post",
    "finance",
    "philosophy",
    "fun",
    "recipe",
    "video",
)


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


def _validated_text_job(action: str, job_fn: Callable[[], object]) -> Callable[[], object]:
    def validated():
        autobot.validate_runtime_config(action)
        return job_fn()

    return validated


def _runner_primary_publish(endpoint: str) -> bool:
    return endpoint in {"me/feed", "me/photos"}


def _adaptive_before_publish(action: str):
    """Build a bounded pre-publish guard for one adaptive content category."""

    def guard(endpoint: str, request_data: dict):
        current = datetime.now(timezone.utc)
        since = (current - timedelta(days=dedup.anti_repeat_days(action))).isoformat()
        recent = content_repository.recent_content(
            db.execute,
            action,
            since,
            30,
        )
        return prepublish_guard.evaluate_request(
            action=action,
            endpoint=endpoint,
            request_data=request_data,
            recent=recent,
            gemini_fn=lambda prompt: autobot.call_gemini(prompt),
            now=current,
        )

    return guard


def _adaptive_publish_callback(action: str):
    def callback(endpoint: str, request_data: dict, response: dict) -> None:
        publication_ledger.record_published_content(
            db.execute,
            action=action,
            endpoint=endpoint,
            request_data=request_data,
            response=response,
        )

    return callback


def _video_publish_callback(**metadata) -> None:
    publication_ledger.record_published_content(
        db.execute,
        action="video",
        **metadata,
    )


def resolve_jobs(dispatch_run_key: str | None = None) -> dict[str, Callable[[], object]]:
    """Resolve jobs through shared DB, verified HTTP and explicit outcome adapters."""
    autobot.execute_db = db.execute
    autobotvideo.db_execute = db.execute
    autobot.http = secure_session_from(autobot.http)
    autobotvideo.http = secure_session_from(autobotvideo.http)
    autobot.send_tele = notifications.send_message

    text_jobs = {
        "post": adapt_publish_job(
            autobot.single_post_job,
            autobot,
            lambda endpoint: endpoint == "me/feed",
            allow_skip=True,
            on_published=_adaptive_publish_callback("post"),
            before_publish=_adaptive_before_publish("post"),
        ),
        "reply": adapt_reply_job(autobot.auto_reply_job, autobot),
        "finance": adapt_publish_job(
            autobot.financial_post_job,
            autobot,
            lambda endpoint: endpoint == "me/feed",
            allow_skip=False,
            on_published=_adaptive_publish_callback("finance"),
            before_publish=_adaptive_before_publish("finance"),
        ),
        "philosophy": adapt_publish_job(
            autobot.philosophy_post_job,
            autobot,
            lambda endpoint: endpoint == "me/feed",
            allow_skip=False,
            on_published=_adaptive_publish_callback("philosophy"),
            before_publish=_adaptive_before_publish("philosophy"),
        ),
        "summary": adapt_publish_job(
            autobot.daily_summary_job,
            autobot,
            lambda endpoint: endpoint.endswith("/photos"),
            allow_skip=True,
        ),
        "veo": adapt_delivery_job(
            autobot.veo_prompt_job,
            autobot,
            allow_skip=True,
        ),
        "recipe": adapt_publish_job(
            runner.recipe_job,
            autobot,
            _runner_primary_publish,
            allow_skip=False,
            on_published=_adaptive_publish_callback("recipe"),
            before_publish=_adaptive_before_publish("recipe"),
        ),
        "fun": adapt_publish_job(
            runner.fun_job,
            autobot,
            _runner_primary_publish,
            allow_skip=False,
            on_published=_adaptive_publish_callback("fun"),
            before_publish=_adaptive_before_publish("fun"),
        ),
    }

    jobs = {
        action: _validated_text_job(action, job_fn)
        for action, job_fn in text_jobs.items()
    }
    jobs["video"] = lambda: autobotvideo.video_post_job(
        dry_run=False,
        on_published=_video_publish_callback,
    )
    jobs["health"] = lambda: health.run_health_check(
        autobot.http,
        autobot.call_gemini,
        db.execute,
    )
    jobs["metrics"] = lambda: metrics_runner.collect_due_metrics()
    jobs["learn"] = lambda: feedback_loop.refresh_strategy(db.execute)
    jobs["report_daily"] = lambda: reporting.send_daily_report(
        db.execute,
        notifications.send_message,
    )
    jobs["report_weekly"] = lambda: reporting.send_weekly_report(
        db.execute,
        notifications.send_message,
    )

    adaptive_content_jobs = {
        action: jobs[action]
        for action in ADAPTIVE_CONTENT_ACTIONS
    }
    jobs["planner"] = lambda: adaptive_jobs.create_daily_plan(db.execute)
    jobs["dispatch"] = lambda: dispatcher.dispatch_due(
        db.execute,
        adaptive_content_jobs,
        run_key=(
            dispatch_run_key
            or make_run_key("dispatch", utc_now_iso())
        ),
    )
    return jobs


def run_action(action: str, jobs: dict[str, Callable[[], object]] | None = None) -> JobOutcome:
    if action not in VALID_ACTIONS:
        raise ValueError(f"Action không hợp lệ: {action}")

    started_at = utc_now_iso()
    run_key = make_run_key(action, started_at)
    if jobs is None:
        jobs = resolve_jobs(dispatch_run_key=run_key)
    if action not in jobs:
        raise ValueError(f"Action không hợp lệ: {action}")

    meta = scheduler.schedule_metadata(os.getenv("SCHEDULED_CRON", ""))
    scheduled_for = meta.scheduled_for.isoformat() if meta.scheduled_for else None

    db.ensure_schema()
    db.record_job(
        run_key,
        action,
        "started",
        started_at,
        None,
        "",
        scheduled_for,
        meta.delay_minutes,
    )

    if meta.stale:
        finished_at = utc_now_iso()
        detail = f"stale schedule: delayed {meta.delay_minutes} minutes"
        db.record_job(
            run_key,
            action,
            "skipped",
            started_at,
            finished_at,
            detail,
            scheduled_for,
            meta.delay_minutes,
        )
        notifications.send_stale(
            action,
            scheduled_for or "unknown",
            meta.delay_minutes or 0,
            github_run_url(),
        )
        return skipped(detail)

    def recorder(status: str, detail: str = "") -> None:
        finished_at = utc_now_iso()
        db.record_job(
            run_key,
            action,
            status,
            started_at,
            finished_at,
            detail[:1000],
            scheduled_for,
            meta.delay_minutes,
        )

    def notifier(failed_action: str, error: Exception) -> None:
        notifications.send_failure(failed_action, error, github_run_url())

    return run_job(action, jobs[action], recorder, notifier)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(
            "Cách dùng: python hardening_runner.py "
            "<post|reply|finance|philosophy|summary|veo|recipe|fun|video|health|metrics|learn|planner|dispatch|report_daily|report_weekly>"
        )
    run_action(sys.argv[1])


if __name__ == "__main__":
    main()
