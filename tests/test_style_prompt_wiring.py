import unittest
from types import SimpleNamespace
from unittest.mock import patch


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

    def test_prompt_adapter_styles_only_primary_content_generation_call(self):
        from app.style_prompt_adapter import run_with_style
        from app.style_strategy import StyleBundle

        class FakeModule:
            def __init__(self):
                self.prompts = []

            def call_gemini(self, prompt, timeout=30):
                self.prompts.append(prompt)
                return "ok"

        bundle = StyleBundle("question", "witty", "opinion_question", "exploit")

        recipe = FakeModule()

        def recipe_job():
            recipe.call_gemini("dish")
            recipe.call_gemini("content")
            recipe.call_gemini("seed")

        run_with_style("recipe", recipe, recipe_job, bundle)
        self.assertNotIn("hook_type=", recipe.prompts[0])
        self.assertIn("hook_type=question", recipe.prompts[1])
        self.assertNotIn("hook_type=", recipe.prompts[2])

        post = FakeModule()
        run_with_style("post", post, lambda: post.call_gemini("content"), bundle)
        self.assertIn("hook_type=question", post.prompts[0])

        post.call_gemini("outside")
        self.assertEqual(post.prompts[-1], "outside")

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
