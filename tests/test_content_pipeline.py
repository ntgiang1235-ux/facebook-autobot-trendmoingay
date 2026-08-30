import json
import unittest
from unittest.mock import patch

from app.content_models import ContentCandidate, RecentContent
from app.quality import QualityDecision


class ContentPipelineTests(unittest.TestCase):
    def make_candidate(
        self,
        *,
        topic_key="gold-new",
        topic_text="Giá vàng và những điểm đáng chú ý hôm nay",
        content_text="3 điểm đáng chú ý về giá vàng hôm nay.",
        source_url="https://example.test/new",
    ):
        return ContentCandidate(
            category="finance",
            topic_key=topic_key,
            topic_text=topic_text,
            content_text=content_text,
            source_url=source_url,
            hook_type="number",
            style_type="explanatory",
            cta_type="opinion_question",
            format_type="text",
        )

    def make_recent(
        self,
        *,
        topic_key="old-topic",
        topic_text="Thị trường chứng khoán tuần qua",
        source_url="https://example.test/old",
    ):
        return RecentContent(
            id=1,
            category="finance",
            topic_key=topic_key,
            topic_text=topic_text,
            content_text="Nội dung cũ",
            source_url=source_url,
            published_at="2026-08-29T08:00:00+00:00",
        )

    @staticmethod
    def semantic_not_duplicate(prompt):
        return json.dumps(
            {"duplicate": False, "similarity": 0.12, "reason": "different topic"}
        )

    def test_exact_duplicate_rejects_without_gemini_or_quality(self):
        from app.content_pipeline import prepare_publishable_candidate

        candidate = self.make_candidate(source_url="https://example.test/same")
        recent = [self.make_recent(source_url="https://example.test/same")]

        def forbidden(*args, **kwargs):
            raise AssertionError("Gemini/rewrite must not be called")

        with patch("app.content_pipeline.assess_draft", side_effect=forbidden):
            result = prepare_publishable_candidate(
                candidate, recent, forbidden, forbidden
            )

        self.assertEqual(result.status, "rejected_duplicate")
        self.assertEqual(result.duplicate_score, 1.0)
        self.assertEqual(result.rewrite_count, 0)

    def test_lexical_duplicate_rejects_without_gemini_or_quality(self):
        from app.content_pipeline import prepare_publishable_candidate

        candidate = self.make_candidate(topic_text="Giá vàng hôm nay tăng mạnh")
        recent = [self.make_recent(topic_text="Giá vàng hôm nay tăng mạnh")]

        def forbidden(*args, **kwargs):
            raise AssertionError("Gemini/rewrite must not be called")

        with patch("app.content_pipeline.assess_draft", side_effect=forbidden):
            result = prepare_publishable_candidate(
                candidate, recent, forbidden, forbidden
            )

        self.assertEqual(result.status, "rejected_duplicate")
        self.assertGreaterEqual(result.duplicate_score, 0.80)

    def test_semantic_duplicate_rejects_before_quality(self):
        from app.content_pipeline import prepare_publishable_candidate

        def semantic_duplicate(prompt):
            return json.dumps(
                {"duplicate": True, "similarity": 0.93, "reason": "same event"}
            )

        with patch(
            "app.content_pipeline.assess_draft",
            side_effect=AssertionError("quality must not run"),
        ):
            result = prepare_publishable_candidate(
                self.make_candidate(),
                [self.make_recent()],
                semantic_duplicate,
                lambda candidate, decision: candidate,
            )

        self.assertEqual(result.status, "rejected_duplicate")
        self.assertEqual(result.duplicate_score, 0.93)
        self.assertEqual(result.detail, "same event")

    def test_quality_publish_returns_ready_without_rewrite(self):
        from app.content_pipeline import prepare_publishable_candidate

        candidate = self.make_candidate()
        with patch(
            "app.content_pipeline.assess_draft",
            return_value=QualityDecision(81.0, "publish", ("good",)),
        ):
            result = prepare_publishable_candidate(
                candidate,
                [self.make_recent()],
                self.semantic_not_duplicate,
                lambda candidate, decision: self.fail("rewrite should not run"),
            )

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.candidate, candidate)
        self.assertEqual(result.quality_score, 81.0)
        self.assertEqual(result.rewrite_count, 0)

    def test_rewrite_once_then_publish_rewritten_candidate(self):
        from app.content_pipeline import prepare_publishable_candidate

        original = self.make_candidate()
        rewritten = self.make_candidate(
            topic_key="gold-rewritten",
            topic_text="Ba diễn biến mới của giá vàng",
            content_text="Ba diễn biến mới đáng chú ý của giá vàng.",
            source_url="https://example.test/rewritten",
        )
        rewrites = []

        def rewrite(candidate, decision):
            rewrites.append((candidate, decision.score))
            return rewritten

        with patch(
            "app.content_pipeline.assess_draft",
            side_effect=[
                QualityDecision(70.0, "rewrite", ("needs work",)),
                QualityDecision(82.0, "publish", ("good",)),
            ],
        ):
            result = prepare_publishable_candidate(
                original,
                [self.make_recent()],
                self.semantic_not_duplicate,
                rewrite,
            )

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.candidate, rewritten)
        self.assertEqual(result.quality_score, 82.0)
        self.assertEqual(result.rewrite_count, 1)
        self.assertEqual(len(rewrites), 1)

    def test_two_rewrites_still_below_threshold_skips_low_quality(self):
        from app.content_pipeline import prepare_publishable_candidate

        counter = {"value": 0}

        def rewrite(candidate, decision):
            counter["value"] += 1
            return self.make_candidate(
                topic_key=f"rewrite-{counter['value']}",
                topic_text=f"Biến thể nội dung {counter['value']}",
                content_text=f"Nội dung viết lại {counter['value']}",
                source_url=f"https://example.test/rewrite-{counter['value']}",
            )

        with patch(
            "app.content_pipeline.assess_draft",
            side_effect=[
                QualityDecision(70.0, "rewrite"),
                QualityDecision(71.0, "rewrite"),
                QualityDecision(72.0, "rewrite"),
            ],
        ):
            result = prepare_publishable_candidate(
                self.make_candidate(),
                [self.make_recent()],
                self.semantic_not_duplicate,
                rewrite,
                max_rewrites=2,
            )

        self.assertEqual(result.status, "skipped_low_quality")
        self.assertEqual(result.rewrite_count, 2)
        self.assertEqual(counter["value"], 2)

    def test_reject_score_does_not_rewrite_same_candidate(self):
        from app.content_pipeline import prepare_publishable_candidate

        rewrites = []
        with patch(
            "app.content_pipeline.assess_draft",
            return_value=QualityDecision(60.0, "reject", ("too weak",)),
        ):
            result = prepare_publishable_candidate(
                self.make_candidate(),
                [self.make_recent()],
                self.semantic_not_duplicate,
                lambda candidate, decision: rewrites.append(candidate),
            )

        self.assertEqual(result.status, "skipped_low_quality")
        self.assertEqual(result.quality_score, 60.0)
        self.assertEqual(result.rewrite_count, 0)
        self.assertEqual(rewrites, [])


if __name__ == "__main__":
    unittest.main()
