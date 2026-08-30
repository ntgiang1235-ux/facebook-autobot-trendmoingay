import unittest
from unittest.mock import patch

import hardening_runner
import runner
from app.prepublish_guard import PrePublishDecision


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


class RunnerPublishTests(unittest.TestCase):
    def _jobs(self):
        return hardening_runner.resolve_jobs()

    def test_fun_http_200_without_post_id_is_failure(self):
        with patch.object(runner.autobot, "call_gemini", return_value="fun status"), patch.object(
            runner, "find_image", return_value=None
        ), patch.object(
            runner.autobot, "call_fb_api", return_value=(200, {})
        ), patch.object(runner.autobot, "validate_runtime_config"), patch.object(
            hardening_runner, "_adaptive_before_publish", return_value=allow_publish
        ):
            jobs = self._jobs()
            with self.assertRaises(RuntimeError):
                jobs["fun"]()

    def test_recipe_http_200_without_post_id_is_failure(self):
        with patch.object(runner.autobot, "call_gemini", return_value="Pasta"), patch.object(
            runner, "find_recipe_image", return_value=None
        ), patch.object(
            runner.autobot, "call_fb_api", return_value=(200, {})
        ), patch.object(runner.autobot, "validate_runtime_config"), patch.object(
            hardening_runner, "_adaptive_before_publish", return_value=allow_publish
        ):
            jobs = self._jobs()
            with self.assertRaises(RuntimeError):
                jobs["recipe"]()

    def test_recipe_seed_comment_failure_after_publish_is_best_effort(self):
        with patch.object(runner.autobot, "call_gemini", return_value="Pasta"), patch.object(
            runner, "find_recipe_image", return_value=None
        ), patch.object(
            runner.autobot,
            "call_fb_api",
            side_effect=[
                (200, {"id": "post-123"}),
                (500, {"error": "seed failed"}),
            ],
        ), patch.object(runner.autobot, "validate_runtime_config"), patch.object(
            hardening_runner, "_adaptive_before_publish", return_value=allow_publish
        ), patch.object(
            hardening_runner.publication_ledger,
            "record_published_content",
            return_value=1,
        ):
            jobs = self._jobs()
            outcome = jobs["recipe"]()
        self.assertEqual(outcome.status, "success")


if __name__ == "__main__":
    unittest.main()
