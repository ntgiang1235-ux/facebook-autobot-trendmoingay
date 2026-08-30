import unittest

from app.content_models import ContentCandidate, RecentContent


class ContentModelTests(unittest.TestCase):
    def test_content_candidate_keeps_strategy_metadata(self):
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


class LocalDedupTests(unittest.TestCase):
    def make_recent(
        self,
        *,
        topic_key="different-topic",
        topic_text="Một chủ đề khác",
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

    def make_candidate(
        self,
        *,
        topic_key="gold-price-new",
        topic_text="Giá vàng hôm nay tiếp tục tăng mạnh",
        source_url="https://example.test/new",
    ):
        return ContentCandidate(
            category="finance",
            topic_key=topic_key,
            topic_text=topic_text,
            content_text="Nội dung mới",
            source_url=source_url,
        )

    def test_normalize_text_handles_case_punctuation_and_whitespace(self):
        from app.dedup import normalize_text

        self.assertEqual(normalize_text("  Giá VÀNG!!!\nHôm nay? "), "giá vàng hôm nay")

    def test_lexical_similarity_distinguishes_related_and_unrelated_topics(self):
        from app.dedup import lexical_similarity

        similar = lexical_similarity(
            "Giá vàng hôm nay tiếp tục tăng mạnh",
            "Giá vàng hôm nay tăng mạnh",
        )
        unrelated = lexical_similarity(
            "Giá vàng hôm nay tiếp tục tăng mạnh",
            "Đội tuyển bóng đá chuẩn bị trận đấu",
        )

        self.assertGreaterEqual(similar, 0.80)
        self.assertLess(unrelated, 0.80)

    def test_category_specific_anti_repeat_windows(self):
        from app.dedup import anti_repeat_days

        self.assertEqual(anti_repeat_days("news"), 7)
        self.assertEqual(anti_repeat_days("finance"), 14)
        self.assertEqual(anti_repeat_days("fun"), 14)
        self.assertEqual(anti_repeat_days("recipe"), 30)
        self.assertEqual(anti_repeat_days("philosophy"), 30)
        self.assertEqual(anti_repeat_days("video"), 30)

    def test_same_source_url_is_exact_duplicate(self):
        from app.dedup import check_local_duplicate

        candidate = self.make_candidate(source_url="https://example.test/same")
        recent = [self.make_recent(source_url="https://example.test/same")]

        decision = check_local_duplicate(candidate, recent)

        self.assertIsNotNone(decision)
        self.assertTrue(decision.duplicate)
        self.assertEqual(decision.layer, "exact")
        self.assertEqual(decision.score, 1.0)

    def test_same_topic_key_is_exact_duplicate(self):
        from app.dedup import check_local_duplicate

        candidate = self.make_candidate(topic_key="gold-price")
        recent = [self.make_recent(topic_key="gold-price")]

        decision = check_local_duplicate(candidate, recent)

        self.assertIsNotNone(decision)
        self.assertTrue(decision.duplicate)
        self.assertEqual(decision.layer, "exact")

    def test_similar_topic_text_is_lexical_duplicate(self):
        from app.dedup import check_local_duplicate

        candidate = self.make_candidate()
        recent = [self.make_recent(topic_text="Giá vàng hôm nay tăng mạnh")]

        decision = check_local_duplicate(candidate, recent)

        self.assertIsNotNone(decision)
        self.assertTrue(decision.duplicate)
        self.assertEqual(decision.layer, "lexical")
        self.assertGreaterEqual(decision.score, 0.80)

    def test_unrelated_recent_content_is_inconclusive_locally(self):
        from app.dedup import check_local_duplicate

        decision = check_local_duplicate(self.make_candidate(), [self.make_recent()])

        self.assertIsNone(decision)


class SemanticDedupTests(unittest.TestCase):
    def make_candidate(self):
        return ContentCandidate(
            category="news",
            topic_key="storm-central-vietnam",
            topic_text="Miền Trung chuẩn bị ứng phó cơn bão đang áp sát",
            content_text="Nội dung mới",
        )

    def make_recent(self, index):
        return RecentContent(
            id=index,
            category="news",
            topic_key=f"topic-{index}",
            topic_text=f"Topic {index} unique marker",
            content_text=f"Recent content {index}",
            source_url=f"https://example.test/{index}",
            published_at="2026-08-29T08:00:00+00:00",
        )

    def test_semantic_duplicate_parses_structured_result(self):
        from app.dedup import check_semantic_duplicate

        captured = []

        def gemini(prompt):
            captured.append(prompt)
            return '{"duplicate": true, "similarity": 0.91, "reason": "same event"}'

        decision = check_semantic_duplicate(
            self.make_candidate(),
            [self.make_recent(1)],
            gemini,
        )

        self.assertTrue(decision.duplicate)
        self.assertEqual(decision.layer, "semantic")
        self.assertAlmostEqual(decision.score, 0.91)
        self.assertEqual(decision.reason, "same event")
        self.assertEqual(len(captured), 1)

    def test_semantic_prompt_is_bounded_to_first_twenty_recent_items(self):
        from app.dedup import check_semantic_duplicate

        captured = []

        def gemini(prompt):
            captured.append(prompt)
            return '{"duplicate": false, "similarity": 0.25, "reason": "different"}'

        decision = check_semantic_duplicate(
            self.make_candidate(),
            [self.make_recent(i) for i in range(25)],
            gemini,
        )

        self.assertFalse(decision.duplicate)
        self.assertIn("Topic 19 unique marker", captured[0])
        self.assertNotIn("Topic 20 unique marker", captured[0])
        self.assertNotIn("Topic 24 unique marker", captured[0])

    def test_semantic_parser_accepts_fenced_json_and_clamps_similarity(self):
        from app.dedup import check_semantic_duplicate

        def gemini(prompt):
            return '```json\n{"duplicate": true, "similarity": 5, "reason": "same"}\n```'

        decision = check_semantic_duplicate(self.make_candidate(), [], gemini)

        self.assertTrue(decision.duplicate)
        self.assertEqual(decision.score, 1.0)
        self.assertEqual(decision.layer, "semantic")

    def test_malformed_semantic_result_is_unavailable_not_duplicate(self):
        from app.dedup import check_semantic_duplicate

        decision = check_semantic_duplicate(
            self.make_candidate(),
            [self.make_recent(1)],
            lambda prompt: "not-json",
        )

        self.assertFalse(decision.duplicate)
        self.assertEqual(decision.score, 0.0)
        self.assertEqual(decision.layer, "semantic_unavailable")
        self.assertEqual(decision.reason, "semantic check unavailable")

    def test_semantic_api_error_is_unavailable_not_exception(self):
        from app.dedup import check_semantic_duplicate

        def gemini(prompt):
            raise RuntimeError("api unavailable")

        decision = check_semantic_duplicate(
            self.make_candidate(),
            [self.make_recent(1)],
            gemini,
        )

        self.assertFalse(decision.duplicate)
        self.assertEqual(decision.layer, "semantic_unavailable")


if __name__ == "__main__":
    unittest.main()
