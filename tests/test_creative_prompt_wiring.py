import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.publication_context import PublicationContext, use_publication_context


class CreativePromptWrapperTests(unittest.TestCase):
    def _context(self):
        return PublicationContext(
            run_key="run-1",
            category="post",
            scheduled_for="2026-08-31T01:30:00+00:00",
            strategy_mode="exploit",
            strategy_version=12,
            hook_type="contrast",
            style_type="explanatory",
            cta_type="opinion_question",
        )

    def test_wrapper_augments_only_matching_primary_prompt_and_restores_gemini(self):
        from app.creative_strategy import run_with_creative_prompt

        seen = []

        def gemini(prompt, timeout=30):
            seen.append((prompt, timeout))
            return "ok"

        module = SimpleNamespace(call_gemini=gemini)
        original = module.call_gemini

        def job():
            module.call_gemini("Hãy gợi ý ngẫu nhiên đúng 1 món ăn")
            module.call_gemini("Viết status FB cho tin: dữ kiện", timeout=11)
            return "done"

        with use_publication_context(self._context()):
            result = run_with_creative_prompt(
                job,
                module,
                markers=("Viết status FB cho tin:",),
            )

        self.assertEqual(result, "done")
        self.assertEqual(seen[0][0], "Hãy gợi ý ngẫu nhiên đúng 1 món ăn")
        self.assertNotIn("CHỈ DẪN PHONG CÁCH ADAPTIVE", seen[0][0])
        self.assertIn("CHỈ DẪN PHONG CÁCH ADAPTIVE", seen[1][0])
        self.assertIn("không bịa", seen[1][0].lower())
        self.assertEqual(seen[1][1], 11)
        self.assertIs(module.call_gemini, original)

    def test_wrapper_without_publication_context_leaves_prompt_unchanged(self):
        from app.creative_strategy import run_with_creative_prompt

        gemini = Mock(return_value="ok")
        module = SimpleNamespace(call_gemini=gemini)

        run_with_creative_prompt(
            lambda: module.call_gemini("Viết status FB cho tin: dữ kiện"),
            module,
            markers=("Viết status FB cho tin:",),
        )

        gemini.assert_called_once_with("Viết status FB cho tin: dữ kiện")

    def test_wrapper_restores_original_gemini_when_business_job_raises(self):
        from app.creative_strategy import run_with_creative_prompt

        original = Mock(return_value="ok")
        module = SimpleNamespace(call_gemini=original)

        def fail():
            module.call_gemini("Viết status FB cho tin: dữ kiện")
            raise RuntimeError("boom")

        with use_publication_context(self._context()):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                run_with_creative_prompt(
                    fail,
                    module,
                    markers=("Viết status FB cho tin:",),
                )

        self.assertIs(module.call_gemini, original)


class ProductionCreativePromptWiringTests(unittest.TestCase):
    def test_all_adaptive_actions_have_targeted_primary_prompt_markers(self):
        import hardening_runner

        self.assertEqual(
            set(hardening_runner.CREATIVE_PROMPT_MARKERS),
            {"post", "finance", "philosophy", "fun", "recipe", "video"},
        )
        self.assertIn("Viết status FB cho tin:", hardening_runner.CREATIVE_PROMPT_MARKERS["post"])
        self.assertIn("cập nhật tỷ giá ngoại tệ", hardening_runner.CREATIVE_PROMPT_MARKERS["finance"])
        self.assertIn("Câu nói hôm nay là:", hardening_runner.CREATIVE_PROMPT_MARKERS["philosophy"])
        self.assertIn("status tấu hài", hardening_runner.CREATIVE_PROMPT_MARKERS["fun"])
        self.assertIn("Chiều nay ăn gì?", hardening_runner.CREATIVE_PROMPT_MARKERS["recipe"])
        self.assertIn("caption Facebook Reels", hardening_runner.CREATIVE_PROMPT_MARKERS["video"])

    def test_post_and_video_jobs_route_through_creative_prompt_wrapper(self):
        import hardening_runner
        from app.job_contract import success

        with patch.object(
            hardening_runner.creative_strategy,
            "run_with_creative_prompt",
            side_effect=lambda job_fn, module, markers: success(markers[0]),
        ) as wrap:
            jobs = hardening_runner.resolve_jobs()
            post_result = jobs["post"]()
            video_result = jobs["video"]()

        self.assertEqual(post_result.status, "success")
        self.assertEqual(video_result.status, "success")
        self.assertEqual(wrap.call_count, 2)
        post_call, video_call = wrap.call_args_list
        self.assertIs(post_call.args[1], hardening_runner.autobot)
        self.assertEqual(post_call.kwargs["markers"], hardening_runner.CREATIVE_PROMPT_MARKERS["post"])
        self.assertIs(video_call.args[1], hardening_runner.autobotvideo)
        self.assertEqual(video_call.kwargs["markers"], hardening_runner.CREATIVE_PROMPT_MARKERS["video"])


if __name__ == "__main__":
    unittest.main()
