import json
import unittest

from app.content_models import ContentCandidate


class StyleClassificationTests(unittest.TestCase):
    def _candidate(self):
        return ContentCandidate(
            category="finance",
            topic_key="gold-price",
            topic_text="Giá vàng hôm nay",
            content_text="3 điều đáng chú ý về giá vàng hôm nay. Bạn nghĩ sao?",
            format_type="text",
        )

    def _quality_payload(self, **overrides):
        payload = {
            "novelty": 90,
            "hook": 90,
            "usefulness": 90,
            "readability": 90,
            "tone": 90,
            "cta": 90,
            "semantic_duplicate": False,
            "hook_too_similar": False,
            "excessive_clickbait": False,
            "repetitive_cta": False,
            "format_length_violation": False,
            "reason": "strong draft",
            "hook_type": "number",
            "style_type": "explanatory",
            "cta_type": "opinion_question",
        }
        payload.update(overrides)
        return payload

    def test_quality_assessment_returns_constrained_style_labels_without_extra_call(self):
        from app.quality import assess_draft

        calls = []

        def gemini(prompt):
            calls.append(prompt)
            return json.dumps(self._quality_payload())

        decision = assess_draft(self._candidate(), [], gemini)

        self.assertEqual(len(calls), 1)
        self.assertEqual(decision.action, "publish")
        self.assertEqual(decision.hook_type, "number")
        self.assertEqual(decision.style_type, "explanatory")
        self.assertEqual(decision.cta_type, "opinion_question")
        self.assertIn("hook_type", calls[0])
        self.assertIn("style_type", calls[0])
        self.assertIn("cta_type", calls[0])

    def test_unknown_labels_degrade_without_invalidating_otherwise_valid_quality_score(self):
        from app.quality import assess_draft

        raw = json.dumps(
            self._quality_payload(
                hook_type="viral_magic",
                style_type="mystery_voice",
                cta_type="spam_everyone",
            )
        )
        decision = assess_draft(self._candidate(), [], lambda prompt: raw)

        self.assertEqual(decision.action, "publish")
        self.assertEqual(decision.hook_type, "unknown")
        self.assertEqual(decision.style_type, "unknown")
        self.assertEqual(decision.cta_type, "none")

    def test_pipeline_attaches_final_assessed_style_metadata_before_publish(self):
        from app.content_pipeline import prepare_publishable_candidate

        candidate = self._candidate()

        def gemini(prompt):
            if "same event/topic" in prompt.lower() or "duplicate" in prompt.lower() and "similarity" in prompt.lower():
                return json.dumps(
                    {
                        "duplicate": False,
                        "similarity": 0.1,
                        "reason": "different",
                    }
                )
            return json.dumps(self._quality_payload())

        result = prepare_publishable_candidate(
            candidate,
            [],
            gemini,
            lambda current, quality: current,
            max_rewrites=0,
        )

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.candidate.hook_type, "number")
        self.assertEqual(result.candidate.style_type, "explanatory")
        self.assertEqual(result.candidate.cta_type, "opinion_question")


if __name__ == "__main__":
    unittest.main()
