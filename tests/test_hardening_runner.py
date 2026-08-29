import os
import unittest
from unittest.mock import patch

import hardening_runner
from app.http import VerifiedSession
from app.job_contract import skipped


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
        ), patch.dict(os.environ, {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1"}, clear=False):
            outcome = hardening_runner.run_action("post", jobs=jobs)

        ensure_schema.assert_called_once()
        self.assertEqual(outcome.status, "success")
        self.assertEqual(records[0], ("123-1-post", "post", "started", "start", None, ""))
        self.assertEqual(records[1], ("123-1-post", "post", "success", "start", "finish", ""))
        notify.assert_not_called()

    def test_skipped_result_is_recorded_without_failure(self):
        jobs = {"summary": lambda: skipped("not enough news")}
        records = []
        with patch.object(hardening_runner.db, "ensure_schema"), patch.object(
            hardening_runner.db, "record_job", side_effect=lambda *args: records.append(args)
        ), patch.object(hardening_runner.notifications, "send_failure") as notify, patch.object(
            hardening_runner, "utc_now_iso", side_effect=["start", "finish"]
        ), patch.dict(os.environ, {"GITHUB_RUN_ID": "124", "GITHUB_RUN_ATTEMPT": "1"}, clear=False):
            outcome = hardening_runner.run_action("summary", jobs=jobs)

        self.assertEqual(outcome.status, "skipped")
        self.assertEqual(records[-1][-2:], ("finish", "not enough news"))
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
            os.environ, {"GITHUB_RUN_ID": "125", "GITHUB_RUN_ATTEMPT": "2"}, clear=False
        ):
            with self.assertRaisesRegex(RuntimeError, "facebook failed"):
                hardening_runner.run_action("video", jobs=jobs)

        self.assertEqual(records[0], ("125-2-video", "video", "started", "start", None, ""))
        self.assertEqual(records[-1], ("125-2-video", "video", "failed", "start", "finish", "facebook failed"))
        notify.assert_called_once()
        args = notify.call_args.args
        self.assertEqual(args[0], "video")
        self.assertIn("facebook failed", str(args[1]))
        self.assertEqual(args[2], "https://github.com/run/1")

    def test_resolve_jobs_routes_shared_db_and_secure_http(self):
        with patch.object(hardening_runner.db, "execute") as execute:
            jobs = hardening_runner.resolve_jobs()
            self.assertIs(hardening_runner.autobot.execute_db, execute)
            self.assertIs(hardening_runner.autobotvideo.db_execute, execute)
            self.assertIsInstance(hardening_runner.autobot.http, VerifiedSession)
            self.assertIsInstance(hardening_runner.autobotvideo.http, VerifiedSession)
        self.assertEqual(
            set(jobs),
            {"post", "reply", "finance", "philosophy", "summary", "veo", "recipe", "fun", "video"},
        )

    def test_resolve_jobs_wraps_legacy_false_green_actions(self):
        jobs = hardening_runner.resolve_jobs()
        self.assertIsNot(jobs["post"], hardening_runner.autobot.single_post_job)
        self.assertIsNot(jobs["reply"], hardening_runner.autobot.auto_reply_job)
        self.assertIsNot(jobs["finance"], hardening_runner.autobot.financial_post_job)
        self.assertIsNot(jobs["philosophy"], hardening_runner.autobot.philosophy_post_job)
        self.assertIsNot(jobs["summary"], hardening_runner.autobot.daily_summary_job)


if __name__ == "__main__":
    unittest.main()
