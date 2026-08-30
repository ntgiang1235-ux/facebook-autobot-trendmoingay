import unittest
from unittest.mock import Mock, patch

import hardening_runner
from app.job_contract import success
from app.prepublish_guard import PrePublishDecision
from app.style_strategy import StyleBundle


TEST_BUNDLE = StyleBundle("question", "conversational", "no_cta", "exploit")


class PrePublishWiringTests(unittest.TestCase):
    def test_resolve_jobs_wires_guard_only_to_adaptive_text_photo_publishers(self):
        captured = {}

        def fake_adapt(job_fn, module, predicate, **kwargs):
            if job_fn is hardening_runner.autobot.single_post_job:
                captured["post"] = kwargs
            elif job_fn is hardening_runner.autobot.financial_post_job:
                captured["finance"] = kwargs
            elif job_fn is hardening_runner.autobot.philosophy_post_job:
                captured["philosophy"] = kwargs
            elif job_fn is hardening_runner.runner.recipe_job:
                captured["recipe"] = kwargs
            elif job_fn is hardening_runner.runner.fun_job:
                captured["fun"] = kwargs
            elif job_fn is hardening_runner.autobot.daily_summary_job:
                captured["summary"] = kwargs
            return lambda: success()

        with patch.object(hardening_runner, "adapt_publish_job", side_effect=fake_adapt):
            hardening_runner.resolve_jobs()

        for action in ("post", "finance", "philosophy", "recipe", "fun"):
            self.assertTrue(callable(captured[action].get("before_publish")), action)
        self.assertIsNone(captured["summary"].get("before_publish"))

    def test_production_post_rejection_never_calls_real_facebook(self):
        original_fb = Mock(return_value=(200, {"id": "should-not-exist"}))
        original_job = hardening_runner.autobot.single_post_job
        hardening_runner.autobot.call_fb_api = original_fb

        def fake_post_job():
            hardening_runner.autobot.call_fb_api(
                "me/feed",
                {"message": "duplicate", "link": "https://example.com/story"},
            )

        rejected = PrePublishDecision(
            publish=False,
            status="rejected_duplicate",
            request_data=None,
            quality_score=None,
            duplicate_score=1.0,
            rewrite_count=0,
            detail="same recent topic",
        )
        try:
            with patch.object(hardening_runner.autobot, "single_post_job", fake_post_job), patch.object(
                hardening_runner.content_repository, "recent_content", return_value=[]
            ), patch.object(
                hardening_runner.prepublish_guard, "evaluate_request", return_value=rejected
            ), patch.object(
                hardening_runner.style_strategy, "choose_style_bundle", return_value=TEST_BUNDLE
            ), patch.object(hardening_runner.autobot, "validate_runtime_config"):
                jobs = hardening_runner.resolve_jobs()
                outcome = jobs["post"]()
        finally:
            hardening_runner.autobot.single_post_job = original_job
            hardening_runner.autobot.call_fb_api = original_fb

        self.assertEqual(outcome.status, "skipped")
        self.assertIn("same recent topic", outcome.detail)
        original_fb.assert_not_called()

    def test_guard_uses_category_specific_recent_window(self):
        decision = PrePublishDecision(
            publish=True,
            status="ready",
            request_data={"message": "ok"},
            quality_score=80.0,
            duplicate_score=0.1,
            rewrite_count=0,
            detail="good",
        )
        with patch.object(
            hardening_runner.content_repository, "recent_content", return_value=[]
        ) as recent, patch.object(
            hardening_runner.prepublish_guard, "evaluate_request", return_value=decision
        ) as evaluate:
            guard = hardening_runner._adaptive_before_publish("recipe")
            result = guard("me/feed", {"message": "draft"})

        self.assertIs(result, decision)
        args = recent.call_args.args
        self.assertIs(args[0], hardening_runner.db.execute)
        self.assertEqual(args[1], "recipe")
        self.assertEqual(args[3], 30)
        evaluate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
