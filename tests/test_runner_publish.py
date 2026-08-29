import unittest
from unittest.mock import patch

import runner


class RunnerPublishTests(unittest.TestCase):
    def test_fun_http_200_without_post_id_is_failure(self):
        with patch.object(runner.autobot, "call_gemini", return_value="fun status"), patch.object(
            runner, "find_image", return_value=None
        ), patch.object(
            runner, "publish_photo_or_text", return_value=(200, {})
        ), patch.object(runner.autobot, "send_tele"):
            with self.assertRaises(RuntimeError):
                runner.fun_job()

    def test_recipe_http_200_without_post_id_is_failure(self):
        with patch.object(runner.autobot, "call_gemini", return_value="Pasta"), patch.object(
            runner, "find_recipe_image", return_value=None
        ), patch.object(
            runner, "publish_photo_or_text", return_value=(200, {})
        ), patch.object(runner.autobot, "send_tele"):
            with self.assertRaises(RuntimeError):
                runner.recipe_job()

    def test_recipe_seed_comment_failure_after_publish_is_best_effort(self):
        with patch.object(runner.autobot, "call_gemini", return_value="Pasta"), patch.object(
            runner, "find_recipe_image", return_value=None
        ), patch.object(
            runner, "publish_photo_or_text", return_value=(200, {"id": "post-123"})
        ), patch.object(
            runner.autobot, "call_fb_api", return_value=(500, {"error": "seed failed"})
        ), patch.object(runner.autobot, "send_tele"):
            runner.recipe_job()


if __name__ == "__main__":
    unittest.main()
