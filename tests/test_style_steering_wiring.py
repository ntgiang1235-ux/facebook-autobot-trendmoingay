import unittest
from types import SimpleNamespace
from unittest.mock import patch

import hardening_runner
from app.content_models import ContentCandidate
from app.style_steering import StyleTarget


class StyleSteeringWiringTests(unittest.TestCase):
    def test_prepublish_guard_restyles_before_content_pipeline(self):
        from app import prepublish_guard

        original = ContentCandidate(
            category="finance",
            topic_key="original",
            topic_text="Original",
            content_text="Original post",
            source_url="https://example.com/story",
            format_type="text",
        )
        restyled = ContentCandidate(
            category="finance",
            topic_key="restyled",
            topic_text="Restyled",
            content_text="3 điểm đáng chú ý. Bạn nghĩ sao?",
            source_url="https://example.com/story",
            format_type="text",
        )
        target = StyleTarget("number", "explanatory", "opinion_question", "exploit")

        ready = SimpleNamespace(
            status="ready",
            candidate=restyled,
            quality_score=88.0,
            duplicate_score=0.1,
            rewrite_count=0,
            detail="ready",
        )
        with patch("app.prepublish_guard._candidate_from_request", return_value=original), patch(
            "app.prepublish_guard.restyle_candidate", return_value=restyled
        ) as restyle, patch(
            "app.prepublish_guard.prepare_publishable_candidate", return_value=ready
        ) as pipeline:
            decision = prepublish_guard.evaluate_request(
                action="finance",
                endpoint="me/feed",
                request_data={"message": "Original post", "link": "https://example.com/story"},
                recent=[],
                gemini_fn=lambda prompt: "unused",
                style_target=target,
            )

        restyle.assert_called_once()
        self.assertIs(pipeline.call_args.args[0], restyled)
        self.assertTrue(decision.publish)
        self.assertEqual(decision.request_data["message"], restyled.content_text)

    def test_no_style_target_skips_restyle_call(self):
        from app import prepublish_guard

        candidate = ContentCandidate(
            category="fun",
            topic_key="fun",
            topic_text="Fun",
            content_text="Một bài vui",
        )
        ready = SimpleNamespace(
            status="ready",
            candidate=candidate,
            quality_score=80.0,
            duplicate_score=0.0,
            rewrite_count=0,
            detail="ready",
        )
        with patch("app.prepublish_guard._candidate_from_request", return_value=candidate), patch(
            "app.prepublish_guard.restyle_candidate"
        ) as restyle, patch(
            "app.prepublish_guard.prepare_publishable_candidate", return_value=ready
        ):
            decision = prepublish_guard.evaluate_request(
                action="fun",
                endpoint="me/feed",
                request_data={"message": "Một bài vui"},
                recent=[],
                gemini_fn=lambda prompt: "unused",
                style_target=None,
            )

        restyle.assert_not_called()
        self.assertTrue(decision.publish)

    def test_hardening_guard_selects_target_and_forwards_to_prepublish(self):
        target = StyleTarget("number", "explanatory", "opinion_question", "exploit")
        with patch("hardening_runner.style_steering.select_style_target", return_value=target) as select, patch(
            "hardening_runner.content_repository.recent_content", return_value=[]
        ), patch(
            "hardening_runner.prepublish_guard.evaluate_request", return_value="decision"
        ) as evaluate:
            guard = hardening_runner._adaptive_before_publish("finance")
            result = guard("me/feed", {"message": "hello"})

        self.assertEqual(result, "decision")
        select.assert_called_once_with(hardening_runner.db.execute)
        self.assertIs(evaluate.call_args.kwargs["style_target"], target)

    def test_style_strategy_failure_falls_back_to_no_target_but_still_runs_gate(self):
        with patch(
            "hardening_runner.style_steering.select_style_target",
            side_effect=RuntimeError("strategy unavailable"),
        ), patch(
            "hardening_runner.content_repository.recent_content", return_value=[]
        ), patch(
            "hardening_runner.prepublish_guard.evaluate_request", return_value="decision"
        ) as evaluate:
            guard = hardening_runner._adaptive_before_publish("finance")
            result = guard("me/feed", {"message": "hello"})

        self.assertEqual(result, "decision")
        self.assertIsNone(evaluate.call_args.kwargs["style_target"])


if __name__ == "__main__":
    unittest.main()
