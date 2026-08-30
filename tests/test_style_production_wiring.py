import unittest
from types import SimpleNamespace
from unittest.mock import patch

import autobot
import hardening_runner

from app.content_models import ContentCandidate
from app.style_strategy import StyleBundle


class StyleProductionWiringTests(unittest.TestCase):
    def test_adaptive_post_uses_one_style_bundle_from_prompt_through_ledger(self):
        bundle = StyleBundle("number", "explanatory", "save_for_later", "explore")
        generated_prompts = []
        ledger_calls = []
        seen_gate_bundles = []

        candidate = ContentCandidate(
            category="post",
            topic_key="topic-key",
            topic_text="Nội dung",
            content_text="Nội dung đã gate",
            source_url="https://example.com/story",
            hook_type=bundle.hook_type,
            style_type=bundle.style_type,
            cta_type=bundle.cta_type,
            format_type="text",
        )
        decision = SimpleNamespace(
            publish=True,
            status="ready",
            request_data={
                "message": candidate.content_text,
                "link": candidate.source_url,
            },
            quality_score=86.0,
            duplicate_score=0.08,
            rewrite_count=1,
            detail="ok",
            candidate=candidate,
        )

        def fake_job():
            text = autobot.call_gemini("PRIMARY CONTENT PROMPT")
            autobot.call_fb_api(
                "me/feed",
                {"message": text, "link": "https://example.com/story"},
            )

        def fake_gemini(prompt, timeout=30):
            generated_prompts.append(prompt)
            return "Bản nháp"

        def fake_gate(**kwargs):
            seen_gate_bundles.append(kwargs.get("style_bundle"))
            return decision

        with patch.object(autobot, "single_post_job", side_effect=fake_job), patch.object(
            autobot, "call_gemini", side_effect=fake_gemini
        ), patch.object(
            autobot, "call_fb_api", return_value=(200, {"id": "post-1"})
        ), patch.object(
            autobot, "validate_runtime_config"
        ), patch.object(
            hardening_runner.content_repository,
            "recent_content",
            return_value=[],
        ), patch.object(
            hardening_runner.style_strategy,
            "choose_style_bundle",
            return_value=bundle,
        ), patch.object(
            hardening_runner.prepublish_guard,
            "evaluate_request",
            side_effect=fake_gate,
        ), patch.object(
            hardening_runner.publication_ledger,
            "record_published_content",
            side_effect=lambda *args, **kwargs: ledger_calls.append(kwargs) or 1,
        ):
            jobs = hardening_runner.resolve_jobs()
            outcome = jobs["post"]()

        self.assertEqual(outcome.status, "success")
        self.assertIn("hook_type=number", generated_prompts[0])
        self.assertIn("style_type=explanatory", generated_prompts[0])
        self.assertIn("cta_type=save_for_later", generated_prompts[0])
        self.assertEqual(seen_gate_bundles, [bundle])
        self.assertEqual(len(ledger_calls), 1)
        self.assertEqual(ledger_calls[0]["candidate"], candidate)
        self.assertEqual(ledger_calls[0]["quality_score"], 86.0)
        self.assertEqual(ledger_calls[0]["duplicate_score"], 0.08)

    def test_summary_remains_outside_style_selection(self):
        with patch.object(
            hardening_runner.style_strategy,
            "choose_style_bundle",
        ) as choose_style:
            hardening_runner.resolve_jobs()["summary"]

        choose_style.assert_not_called()


if __name__ == "__main__":
    unittest.main()
