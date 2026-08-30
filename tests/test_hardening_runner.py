import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import hardening_runner
from app.http import VerifiedSession
from app.job_contract import skipped, success


class HardeningRunnerTests(unittest.TestCase):
    def test_invalid_action_is_rejected_before_schema_work(self):
        with patch.object(hardening_runner.db, "ensure_schema") as ensure_schema:
            with self.assertRaisesRegex(ValueError, "Action không hợp lệ"):
                hardening_runner.run_action("unknown", jobs={})
        ensure_schema.assert_not_called()

    def test_success_records_started_then_success(self):
        jobs = {"post": lambda: None}
        records = []
        with patch.object(hardening_runner.db, "ensure_schema") as ensure_schema, patch.object(
            hardening_runner.db, "record_job", side_effect=lambda *args: records.append(args)
        ), patch.object(hardening_runner.notifications, "send_failure") as notify, patch.object(
            hardening_runner, "utc_now_iso", side_effect=["start", "finish"]
        ), patch.dict(os.environ, {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "SCHEDULED_CRON": ""}, clear=False):
            outcome = hardening_runner.run_action("post", jobs=jobs)

        ensure_schema.assert_called_once()
        self.assertEqual(outcome.status, "success")
        self.assertEqual(records[0], ("123-1-post", "post", "started", "start", None, "", None, None))
        self.assertEqual(records[1], ("123-1-post", "post", "success", "start", "finish", "", None, None))
        notify.assert_not_called()

    def test_skipped_result_is_recorded_without_failure(self):
        jobs = {"summary": lambda: skipped("not enough news")}
        records = []
        with patch.object(hardening_runner.db, "ensure_schema"), patch.object(
            hardening_runner.db, "record_job", side_effect=lambda *args: records.append(args)
        ), patch.object(hardening_runner.notifications, "send_failure") as notify, patch.object(
            hardening_runner, "utc_now_iso", side_effect=["start", "finish"]
        ), patch.dict(os.environ, {"GITHUB_RUN_ID": "124", "GITHUB_RUN_ATTEMPT": "1", "SCHEDULED_CRON": ""}, clear=False):
            outcome = hardening_runner.run_action("summary", jobs=jobs)

        self.assertEqual(outcome.status, "skipped")
        self.assertEqual(records[-1][4:6], ("finish", "not enough news"))
        notify.assert_not_called()

    def test_failure_records_failed_notifies_and_reraises(self):
        def fail():
            raise RuntimeError("facebook failed")

        jobs = {"video": fail}
        records = []
        with patch.object(hardening_runner.db, "ensure_schema"), patch.object(
            hardening_runner.db, "record_job", side_effect=lambda *args: records.append(args)
        ), patch.object(hardening_runner.notifications, "send_failure") as notify, patch.object(
            hardening_runner, "utc_now_iso", side_effect=["start", "finish"]
        ), patch.object(hardening_runner, "github_run_url", return_value="https://github.com/run/1"), patch.dict(
            os.environ, {"GITHUB_RUN_ID": "125", "GITHUB_RUN_ATTEMPT": "2", "SCHEDULED_CRON": ""}, clear=False
        ):
            with self.assertRaisesRegex(RuntimeError, "facebook failed"):
                hardening_runner.run_action("video", jobs=jobs)

        self.assertEqual(records[0], ("125-2-video", "video", "started", "start", None, "", None, None))
        self.assertEqual(records[-1], ("125-2-video", "video", "failed", "start", "finish", "facebook failed", None, None))
        notify.assert_called_once()
        args = notify.call_args.args
        self.assertEqual(args[0], "video")
        self.assertIn("facebook failed", str(args[1]))
        self.assertEqual(args[2], "https://github.com/run/1")

    def test_stale_schedule_is_skipped_before_job_execution(self):
        job = Mock()
        records = []
        scheduled_for = datetime(2026, 8, 29, 9, 7, tzinfo=timezone.utc)
        meta = SimpleNamespace(scheduled_for=scheduled_for, delay_minutes=304, stale=True)

        with patch.object(hardening_runner.db, "ensure_schema"), patch.object(
            hardening_runner.db, "record_job", side_effect=lambda *args: records.append(args)
        ), patch.object(hardening_runner.scheduler, "schedule_metadata", return_value=meta), patch.object(
            hardening_runner.notifications, "send_stale"
        ) as send_stale, patch.object(
            hardening_runner, "utc_now_iso", side_effect=["start", "finish"]
        ), patch.object(hardening_runner, "github_run_url", return_value="https://github.com/run/stale"), patch.dict(
            os.environ,
            {"GITHUB_RUN_ID": "126", "GITHUB_RUN_ATTEMPT": "1", "SCHEDULED_CRON": "7 9 * * *"},
            clear=False,
        ):
            outcome = hardening_runner.run_action("recipe", jobs={"recipe": job})

        self.assertEqual(outcome.status, "skipped")
        self.assertIn("304", outcome.detail)
        job.assert_not_called()
        self.assertEqual(records[-1][-2:], (scheduled_for.isoformat(), 304))
        send_stale.assert_called_once_with("recipe", scheduled_for.isoformat(), 304, "https://github.com/run/stale")

    def test_resolve_jobs_routes_shared_db_and_secure_http(self):
        with patch.object(hardening_runner.db, "execute") as execute:
            jobs = hardening_runner.resolve_jobs()
            self.assertIs(hardening_runner.autobot.execute_db, execute)
            self.assertIs(hardening_runner.autobotvideo.db_execute, execute)
            self.assertIsInstance(hardening_runner.autobot.http, VerifiedSession)
            self.assertIsInstance(hardening_runner.autobotvideo.http, VerifiedSession)
        self.assertEqual(
            set(jobs),
            {
                "post", "reply", "finance", "philosophy", "summary", "veo",
                "recipe", "fun", "video", "health", "metrics", "learn", "planner", "dispatch",
                "report_daily", "report_weekly"
            },
        )

    def test_health_job_uses_shared_dependency_checks(self):
        with patch.object(hardening_runner.health, "run_health_check", return_value=["turso"]) as check:
            jobs = hardening_runner.resolve_jobs()
            jobs["health"]()

        check.assert_called_once()

    def test_metrics_job_routes_to_dedicated_collector(self):
        with patch.object(hardening_runner.metrics_runner, "collect_due_metrics", return_value={"due": 0, "processed": 0, "failed": 0}) as collect:
            jobs = hardening_runner.resolve_jobs()
            result = jobs["metrics"]()

        collect.assert_called_once_with()
        self.assertEqual(result, {"due": 0, "processed": 0, "failed": 0})

    def test_reporting_jobs_route_shared_db_and_telegram(self):
        with patch.object(
            hardening_runner.reporting, "send_daily_report", return_value=success("daily")
        ) as daily, patch.object(
            hardening_runner.reporting, "send_weekly_report", return_value=success("weekly")
        ) as weekly:
            jobs = hardening_runner.resolve_jobs()
            self.assertEqual(jobs["report_daily"]().status, "success")
            self.assertEqual(jobs["report_weekly"]().status, "success")

        daily.assert_called_once_with(hardening_runner.db.execute, hardening_runner.notifications.send_message)
        weekly.assert_called_once_with(hardening_runner.db.execute, hardening_runner.notifications.send_message)

    def test_resolve_jobs_routes_planner_and_dispatcher_without_operational_jobs(self):
        with patch.object(
            hardening_runner.adaptive_jobs, "create_daily_plan", return_value=success("planned")
        ) as create_plan, patch.object(
            hardening_runner.dispatcher, "dispatch_due", return_value=skipped("no due plan slot")
        ) as dispatch:
            jobs = hardening_runner.resolve_jobs(dispatch_run_key="dispatch-owner")
            planner_outcome = jobs["planner"]()
            dispatch_outcome = jobs["dispatch"]()

        self.assertEqual(planner_outcome.status, "success")
        self.assertEqual(dispatch_outcome.status, "skipped")
        create_plan.assert_called_once_with(hardening_runner.db.execute)
        args = dispatch.call_args.args
        kwargs = dispatch.call_args.kwargs
        self.assertIs(args[0], hardening_runner.db.execute)
        self.assertEqual(
            set(args[1]),
            {"post", "finance", "philosophy", "fun", "recipe", "video"},
        )
        self.assertEqual(kwargs["run_key"], "dispatch-owner")

    def test_run_action_passes_outer_dispatch_run_key_to_resolver(self):
        records = []
        with patch.object(
            hardening_runner,
            "resolve_jobs",
            return_value={"dispatch": lambda: skipped("no due plan slot")},
        ) as resolve, patch.object(hardening_runner.db, "ensure_schema"), patch.object(
            hardening_runner.db, "record_job", side_effect=lambda *args: records.append(args)
        ), patch.object(
            hardening_runner, "utc_now_iso", side_effect=["start", "finish"]
        ), patch.dict(
            os.environ,
            {"GITHUB_RUN_ID": "777", "GITHUB_RUN_ATTEMPT": "2", "SCHEDULED_CRON": ""},
            clear=False,
        ):
            outcome = hardening_runner.run_action("dispatch")

        self.assertEqual(outcome.status, "skipped")
        resolve.assert_called_once_with(dispatch_run_key="777-2-dispatch")
        self.assertEqual(records[0][0], "777-2-dispatch")

    def test_resolve_jobs_wraps_legacy_false_green_actions(self):
        jobs = hardening_runner.resolve_jobs()
        self.assertIsNot(jobs["post"], hardening_runner.autobot.single_post_job)
        self.assertIsNot(jobs["reply"], hardening_runner.autobot.auto_reply_job)
        self.assertIsNot(jobs["finance"], hardening_runner.autobot.financial_post_job)
        self.assertIsNot(jobs["philosophy"], hardening_runner.autobot.philosophy_post_job)
        self.assertIsNot(jobs["summary"], hardening_runner.autobot.daily_summary_job)

    def test_text_jobs_preserve_runtime_config_validation(self):
        with patch.object(hardening_runner.autobot, "validate_runtime_config") as validate, patch.object(
            hardening_runner.autobot, "single_post_job", return_value=None
        ):
            jobs = hardening_runner.resolve_jobs()
            outcome = jobs["post"]()

        validate.assert_called_once_with("post")
        self.assertEqual(outcome.status, "skipped")

    def test_veo_routes_shared_telegram_and_fails_on_delivery_failure(self):
        with patch.object(hardening_runner.autobot, "validate_runtime_config") as validate, patch.object(
            hardening_runner.notifications, "send_message", return_value=False
        ) as send_message, patch.object(
            hardening_runner.autobot,
            "veo_prompt_job",
            side_effect=lambda: hardening_runner.autobot.send_tele("<b>veo</b>"),
        ):
            jobs = hardening_runner.resolve_jobs()
            self.assertIs(hardening_runner.autobot.send_tele, send_message)
            with self.assertRaisesRegex(RuntimeError, "Telegram delivery failed"):
                jobs["veo"]()

        validate.assert_called_once_with("veo")
        send_message.assert_called_once_with("<b>veo</b>")


if __name__ == "__main__":
    unittest.main()
