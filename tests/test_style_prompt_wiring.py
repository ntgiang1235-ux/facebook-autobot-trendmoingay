import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import autobot
import runner


class StylePromptWiringTests(unittest.TestCase):
    def test_style_context_is_scoped_and_prompt_helper_is_noop_outside_context(self):
        from app.style_context import adaptive_prompt, current_style_bundle, use_style_bundle
        from app.style_strategy import StyleBundle

        bundle = StyleBundle("question", "witty", "opinion_question", "exploit")
        self.assertIsNone(current_style_bundle())
        self.assertEqual(adaptive_prompt("BASE"), "BASE")

        with use_style_bundle(bundle):
            self.assertEqual(current_style_bundle(), bundle)
            decorated = adaptive_prompt("BASE")
            self.assertTrue(decorated.startswith("BASE"))
            self.assertIn("hook_type=question", decorated)
            self.assertIn("style_type=witty", decorated)
            self.assertIn("cta_type=opinion_question", decorated)

        self.assertIsNone(current_style_bundle())

    def test_only_adaptive_text_photo_generation_jobs_use_style_prompt_helper(self):
        for fn in (
            autobot.single_post_job,
            autobot.financial_post_job,
            autobot.philosophy_post_job,
            runner.fun_job,
            runner.recipe_job,
        ):
            self.assertIn("adaptive_prompt(prompt)", inspect.getsource(fn), fn.__name__)

        self.assertNotIn("adaptive_prompt", inspect.getsource(autobot.daily_summary_job))
        self.assertNotIn("adaptive_prompt(dish_prompt)", inspect.getsource(runner.recipe_job))

    def test_prepublish_candidate_carries_selected_style_bundle(self):
        from app import prepublish_guard
        from app.style_strategy import StyleBundle

        bundle = StyleBundle("number", "explanatory", "save_for_later", "explore")

        def ready(candidate, recent, gemini_fn, rewrite_fn, max_rewrites=2):
            return SimpleNamespace(
                status="ready",
                candidate=candidate,
                quality_score=88.0,
                duplicate_score=0.05,
                detail="ok",
                rewrite_count=0,
            )

        with patch.object(prepublish_guard, "prepare_publishable_candidate", side_effect=ready):
            decision = prepublish_guard.evaluate_request(
                action="finance",
                endpoint="me/feed",
                request_data={"message": "Nội dung tài chính"},
                recent=[],
                gemini_fn=lambda _prompt: "unused",
                style_bundle=bundle,
            )

        self.assertTrue(decision.publish)
        self.assertEqual(decision.candidate.hook_type, "number")
        self.assertEqual(decision.candidate.style_type, "explanatory")
        self.assertEqual(decision.candidate.cta_type, "save_for_later")
        self.assertEqual(decision.quality_score, 88.0)
        self.assertEqual(decision.duplicate_score, 0.05)


if __name__ == "__main__":
    unittest.main()
