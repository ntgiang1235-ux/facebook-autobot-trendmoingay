import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from app.content_models import RecentContent


class PrePublishGuardTests(unittest.TestCase):
    def test_duplicate_is_skipped_without_returning_publish_data(self):
        from app.prepublish_guard import evaluate_request

        recent = [
            RecentContent(
                id=1,
                category="post",
                topic_key="same-topic",
                topic_text="Giá vàng tăng mạnh hôm nay",
                content_text="Giá vàng tăng mạnh hôm nay",
                source_url="https://example.com/a",
                published_at="2026-08-30T00:00:00+00:00",
            )
        ]
        gemini = Mock(return_value='{"duplicate": false, "similarity": 0.1, "reason": "new"}')
        decision = evaluate_request(
            action="post",
            endpoint="me/feed",
            request_data={"message": "Giá vàng tăng mạnh hôm nay", "link": "https://example.com/a"},
            recent=recent,
            gemini_fn=gemini,
            now=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
        )

        self.assertFalse(decision.publish)
        self.assertEqual(decision.status, "rejected_duplicate")
        self.assertIsNone(decision.request_data)
        gemini.assert_not_called()

    def test_ready_candidate_can_rewrite_message_before_publish(self):
        from app.prepublish_guard import evaluate_request

        responses = iter([
            '{"duplicate": false, "similarity": 0.2, "reason": "different"}',
            '{"novelty": 70, "hook": 70, "usefulness": 70, "readability": 70, "tone": 70, "cta": 70, "semantic_duplicate": false, "hook_too_similar": false, "excessive_clickbait": false, "repetitive_cta": false, "format_length_violation": false, "reason": "needs stronger hook"}',
            "Bản viết lại tốt hơn",
            '{"duplicate": false, "similarity": 0.2, "reason": "different"}',
            '{"novelty": 85, "hook": 85, "usefulness": 85, "readability": 85, "tone": 85, "cta": 85, "semantic_duplicate": false, "hook_too_similar": false, "excessive_clickbait": false, "repetitive_cta": false, "format_length_violation": false, "reason": "good"}',
        ])

        decision = evaluate_request(
            action="finance",
            endpoint="me/feed",
            request_data={"message": "Bản nháp", "link": "https://example.com/story"},
            recent=[],
            gemini_fn=lambda prompt: next(responses),
            now=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
        )

        self.assertTrue(decision.publish)
        self.assertEqual(decision.status, "ready")
        self.assertEqual(decision.request_data["message"], "Bản viết lại tốt hơn")
        self.assertEqual(decision.request_data["link"], "https://example.com/story")
        self.assertEqual(decision.rewrite_count, 1)
        self.assertGreaterEqual(decision.quality_score, 75)

    def test_missing_message_fails_closed_without_gemini(self):
        from app.prepublish_guard import evaluate_request

        gemini = Mock()
        decision = evaluate_request(
            action="post",
            endpoint="me/feed",
            request_data={"link": "https://example.com/story"},
            recent=[],
            gemini_fn=gemini,
            now=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
        )

        self.assertFalse(decision.publish)
        self.assertEqual(decision.status, "skipped_low_quality")
        gemini.assert_not_called()


if __name__ == "__main__":
    unittest.main()
