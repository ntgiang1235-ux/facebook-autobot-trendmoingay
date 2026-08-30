import unittest


class ContentModelTests(unittest.TestCase):
    def test_content_candidate_keeps_strategy_metadata(self):
        from app.content_models import ContentCandidate

        item = ContentCandidate(
            category="finance",
            topic_key="gold-price",
            topic_text="Giá vàng tăng",
            content_text="Nội dung",
            source_url="https://example.test/a",
            source_title="Giá vàng",
            hook_type="number",
            style_type="explanatory",
            cta_type="opinion_question",
            format_type="text",
        )

        self.assertEqual(item.category, "finance")
        self.assertEqual(item.topic_key, "gold-price")
        self.assertEqual(item.hook_type, "number")
        self.assertEqual(item.style_type, "explanatory")
        self.assertEqual(item.cta_type, "opinion_question")
        self.assertEqual(item.format_type, "text")


if __name__ == "__main__":
    unittest.main()
