import unittest

from app.content_models import ContentCandidate


class ContentRepositoryTests(unittest.TestCase):
    def test_content_hash_normalizes_case_and_whitespace(self):
        from app.content_repository import content_hash

        self.assertEqual(content_hash("  Xin   chào "), content_hash("xin chào"))

    def test_record_candidate_uses_parameterized_insert_and_returns_id(self):
        from app.content_repository import record_candidate

        calls = []

        def execute(query, params=()):
            calls.append((query, params))
            return [(17,)]

        candidate = ContentCandidate(
            category="finance",
            topic_key="gold-price",
            topic_text="Giá vàng tăng",
            content_text="Nội dung bài viết",
            source_url="https://example.test/gold",
            source_title="Giá vàng",
            hook_type="number",
            style_type="explanatory",
            cta_type="opinion_question",
            format_type="text",
        )

        content_id = record_candidate(
            execute,
            candidate,
            run_key="run-1",
            action="finance",
            strategy_mode="explore",
            strategy_version=3,
            created_at="2026-08-30T01:00:00+00:00",
        )

        self.assertEqual(content_id, 17)
        query, params = calls[0]
        self.assertIn("INSERT INTO content_posts", query)
        self.assertIn("RETURNING id", query)
        self.assertNotIn(candidate.content_text, query)
        self.assertIn(candidate.content_text, params)
        self.assertIn("run-1", params)
        self.assertIn("explore", params)
        self.assertIn(3, params)

    def test_recent_content_maps_rows_and_binds_filters(self):
        from app.content_repository import recent_content

        calls = []

        def execute(query, params=()):
            calls.append((query, params))
            return [
                (
                    9,
                    "finance",
                    "gold-price",
                    "Giá vàng tăng",
                    "Nội dung",
                    "https://example.test/gold",
                    "2026-08-29T08:00:00+00:00",
                )
            ]

        rows = recent_content(
            execute,
            "finance",
            "2026-08-16T00:00:00+00:00",
            limit=20,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, 9)
        self.assertEqual(rows[0].topic_key, "gold-price")
        query, params = calls[0]
        self.assertIn("ORDER BY published_at DESC", query)
        self.assertEqual(params, ("finance", "2026-08-16T00:00:00+00:00", 20))

    def test_mark_published_and_rejected_update_only_requested_id(self):
        from app.content_repository import mark_published, mark_rejected

        calls = []

        def execute(query, params=()):
            calls.append((query, params))
            return []

        mark_published(execute, 5, "123_456", "2026-08-30T02:00:00+00:00")
        mark_rejected(execute, 6, "duplicate", duplicate_score=0.91)

        publish_query, publish_params = calls[0]
        reject_query, reject_params = calls[1]
        self.assertIn("WHERE id = ?", publish_query)
        self.assertEqual(publish_params[-1], 5)
        self.assertIn("WHERE id = ?", reject_query)
        self.assertEqual(reject_params[-1], 6)
        self.assertIn(0.91, reject_params)


if __name__ == "__main__":
    unittest.main()
