import unittest
from unittest.mock import patch

import hardening_runner
from app.prepublish_guard import PrePublishDecision
from app.style_strategy import StyleBundle


TEST_BUNDLE = StyleBundle("question", "conversational", "no_cta", "exploit")


def allow_publish(endpoint, request_data):
    del endpoint
    return PrePublishDecision(
        publish=True,
        status="ready",
        request_data=dict(request_data),
        quality_score=80.0,
        duplicate_score=0.1,
        rewrite_count=0,
        detail="test fixture ready",
    )


class ProductionLedgerWiringTests(unittest.TestCase):
    def test_adaptive_text_publish_is_written_to_content_ledger(self):
        def legacy_post():
            hardening_runner.autobot.call_fb_api(
                "me/feed",
                {"message": "Tin thật đã đăng", "link": "https://example.com/news"},
            )

        with patch.object(
            hardening_runner.autobot, "validate_runtime_config"
        ), patch.object(
            hardening_runner.autobot, "single_post_job", side_effect=legacy_post
        ), patch.object(
            hardening_runner.autobot,
            "call_fb_api",
            return_value=(200, {"id": "page_post_1"}),
        ), patch.object(
            hardening_runner.style_strategy, "choose_style_bundle", return_value=TEST_BUNDLE
        ), patch.object(
            hardening_runner,
            "_adaptive_before_publish",
            return_value=allow_publish,
        ), patch.object(
            hardening_runner.publication_ledger,
            "record_published_content",
            return_value=41,
        ) as record:
            jobs = hardening_runner.resolve_jobs()
            outcome = jobs["post"]()

        self.assertEqual(outcome.status, "success")
        record.assert_called_once()
        kwargs = record.call_args.kwargs
        self.assertIs(record.call_args.args[0], hardening_runner.db.execute)
        self.assertEqual(kwargs["action"], "post")
        self.assertEqual(kwargs["endpoint"], "me/feed")
        self.assertEqual(kwargs["request_data"]["message"], "Tin thật đã đăng")
        self.assertEqual(kwargs["response"]["id"], "page_post_1")

    def test_operational_summary_publish_is_not_written_to_adaptive_ledger(self):
        def legacy_summary():
            hardening_runner.autobot.call_fb_api(
                "page/photos",
                {"message": "Bản tin cuối ngày"},
                files={"source": object()},
            )

        with patch.object(
            hardening_runner.autobot, "validate_runtime_config"
        ), patch.object(
            hardening_runner.autobot, "daily_summary_job", side_effect=legacy_summary
        ), patch.object(
            hardening_runner.autobot,
            "call_fb_api",
            return_value=(200, {"post_id": "summary_1"}),
        ), patch.object(
            hardening_runner.publication_ledger, "record_published_content"
        ) as record:
            jobs = hardening_runner.resolve_jobs()
            outcome = jobs["summary"]()

        self.assertEqual(outcome.status, "success")
        record.assert_not_called()

    def test_video_job_receives_ledger_callback_from_hardening_runner(self):
        with patch.object(
            hardening_runner.autobotvideo, "video_post_job", return_value=None
        ) as video_job:
            jobs = hardening_runner.resolve_jobs()
            jobs["video"]()

        video_job.assert_called_once()
        kwargs = video_job.call_args.kwargs
        self.assertFalse(kwargs["dry_run"])
        callback = kwargs["on_published"]
        self.assertTrue(callable(callback))


if __name__ == "__main__":
    unittest.main()
