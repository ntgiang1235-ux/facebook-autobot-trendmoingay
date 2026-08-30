import json
import unittest

from app.content_models import ContentCandidate, RecentContent


class QualityScoringTests(unittest.TestCase):
    def test_weighted_quality_score(self):
        from app.quality import QualityRubric, combine_quality_score

        rubric = QualityRubric(
            novelty=80,
            hook=70,
            usefulness=90,
            readability=80,
            tone=80,
            cta=70,
        )

        self.assertEqual(combine_quality_score(rubric, []), 79.0)

    def test_thresholds(self):
        from app.quality import decision_for_score

        self.assertEqual(decision_for_score(75).action, "publish")
        self.assertEqual(decision_for_score(70).action, "rewrite")
        self.assertEqual(decision_for_score(64.9).action, "reject")

    def test_components_and_final_score_are_clamped(self):
        from app.quality import QualityRubric, combine_quality_score

        rubric = QualityRubric(200, -20, 100, 100, 100, 100)
        score = combine_quality_score(rubric, [])

        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 100.0)


class GeminiQualityTests(unittest.TestCase):
    def make_candidate(self):
        return ContentCandidate(
            category="finance",
            topic_key="gold-price",
            topic_text="Giá vàng hôm nay",
            content_text="3 điều đáng chú ý về giá vàng hôm nay.",
            hook_type="number",
            style_type="explanatory",
            cta_type="opinion_question",
            format_type="text",
        )

    def make_recent(self, index):
        return RecentContent(
            id=index,
            category="finance",
            topic_key=f"topic-{index}",
            topic_text=f"Chủ đề gần đây {index}",
            content_text=f"Nội dung {index}",
            source_url=None,
            published_at="2026-08-29T08:00:00+00:00",
        )

    def test_assessment_applies_explicit_penalties(self):
        from app.quality import assess_draft

        payload = {
            "novelty": 100,
            "hook": 100,
            "usefulness": 100,
            "readability": 100,
            "tone": 100,
            "cta": 100,
            "semantic_duplicate": True,
            "hook_too_similar": True,
            "excessive_clickbait": True,
            "repetitive_cta": True,
            "format_length_violation": True,
            "reason": "Too repetitive",
        }

        decision = assess_draft(
            self.make_candidate(),
            [],
            lambda prompt: json.dumps(payload),
        )

        self.assertEqual(decision.score, 0.0)
        self.assertEqual(decision.action, "reject")
        self.assertIn("semantic_duplicate", decision.reasons)
        self.assertIn("hook_too_similar", decision.reasons)
        self.assertIn("excessive_clickbait", decision.reasons)
        self.assertIn("repetitive_cta", decision.reasons)
        self.assertIn("format_length_violation", decision.reasons)

    def test_assessment_accepts_fenced_json_and_caps_recent_examples(self):
        from app.quality import assess_draft

        prompts = []
        payload = {
            "novelty": 82,
            "hook": 80,
            "usefulness": 84,
            "readability": 86,
            "tone": 82,
            "cta": 78,
            "semantic_duplicate": False,
            "hook_too_similar": False,
            "excessive_clickbait": False,
            "repetitive_cta": False,
            "format_length_violation": False,
            "reason": "Good",
        }

        def gemini(prompt):
            prompts.append(prompt)
            return "```json\n" + json.dumps(payload) + "\n```"

        decision = assess_draft(
            self.make_candidate(),
            [self.make_recent(i) for i in range(20)],
            gemini,
        )

        self.assertEqual(decision.action, "publish")
        self.assertEqual(len(prompts), 1)
        self.assertIn("Chủ đề gần đây 11", prompts[0])
        self.assertNotIn("Chủ đề gần đây 12", prompts[0])

    def test_malformed_assessment_is_conservative_rewrite(self):
        from app.quality import assess_draft

        decision = assess_draft(self.make_candidate(), [], lambda prompt: "not json")

        self.assertEqual(decision.score, 65.0)
        self.assertEqual(decision.action, "rewrite")
        self.assertEqual(decision.reasons, ("quality_assessment_unavailable",))

    def test_assessment_api_error_is_conservative_rewrite(self):
        from app.quality import assess_draft

        def gemini(prompt):
            raise RuntimeError("temporary failure")

        decision = assess_draft(self.make_candidate(), [], gemini)

        self.assertEqual(decision.score, 65.0)
        self.assertEqual(decision.action, "rewrite")
        self.assertEqual(decision.reasons, ("quality_assessment_unavailable",))


if __name__ == "__main__":
    unittest.main()
