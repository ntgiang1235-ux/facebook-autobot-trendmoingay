import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch


class PublicationLedgerTests(unittest.TestCase):
    def test_text_publish_records_real_post_id_content_source_and_dispatch_metadata(self):
        from app.publication_context import PublicationContext
        from app.publication_ledger import record_published_content

        execute = Mock()
        context = PublicationContext(
            run_key="dispatch-9",
            category="post",
            scheduled_for="2026-08-31T01:30:00+00:00",
            strategy_mode="exploit",
            strategy_version=12,
        )
        now = datetime(2026, 8, 31, 1, 38, tzinfo=timezone.utc)

        with patch("app.publication_ledger.content_repository.record_candidate", return_value=51) as record:
            content_id = record_published_content(
                execute,
                action="post",
                endpoint="me/feed",
                request_data={
                    "message": "Bản tin sáng nay",
                    "link": "https://example.com/story",
                },
                response={"id": "page_123"},
                context=context,
                now=now,
            )

        self.assertEqual(content_id, 51)
        candidate = record.call_args.args[1]
        self.assertEqual(candidate.category, "post")
        self.assertEqual(candidate.content_text, "Bản tin sáng nay")
        self.assertEqual(candidate.source_url, "https://example.com/story")
        self.assertEqual(candidate.format_type, "text")
        self.assertTrue(candidate.topic_key.startswith("post:"))
        kwargs = record.call_args.kwargs
        self.assertEqual(kwargs["run_key"], "dispatch-9")
        self.assertEqual(kwargs["facebook_post_id"], "page_123")
        self.assertEqual(kwargs["scheduled_for"], context.scheduled_for)
        self.assertEqual(kwargs["strategy_mode"], "exploit")
        self.assertEqual(kwargs["strategy_version"], 12)
        self.assertEqual(kwargs["status"], "published")
        self.assertEqual(kwargs["published_at"], now.isoformat())

    def test_photo_publish_does_not_treat_media_url_as_article_source(self):
        from app.publication_ledger import record_published_content

        with patch("app.publication_ledger.content_repository.record_candidate", return_value=7) as record:
            record_published_content(
                Mock(),
                action="recipe",
                endpoint="me/photos",
                request_data={
                    "message": "Carbonara cho tối nay",
                    "url": "https://images.pexels.com/food.jpg",
                },
                response={"post_id": "page_456"},
                now=datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc),
            )

        candidate = record.call_args.args[1]
        self.assertEqual(candidate.category, "recipe")
        self.assertIsNone(candidate.source_url)
        self.assertEqual(candidate.format_type, "photo")
        self.assertEqual(record.call_args.kwargs["facebook_post_id"], "page_456")

    def test_explicit_video_metadata_is_preserved_without_fabricating_style_fields(self):
        from app.publication_ledger import record_published_content

        with patch("app.publication_ledger.content_repository.record_candidate", return_value=8) as record:
            record_published_content(
                Mock(),
                action="video",
                endpoint="me/videos",
                request_data={"message": "Caption reel"},
                response={"id": "video-789"},
                topic_text="Vietnamese food",
                source_url="https://www.pexels.com/video/789/",
                format_type="video",
                now=datetime(2026, 8, 31, 10, 30, tzinfo=timezone.utc),
            )

        candidate = record.call_args.args[1]
        self.assertEqual(candidate.topic_text, "Vietnamese food")
        self.assertEqual(candidate.source_url, "https://www.pexels.com/video/789/")
        self.assertEqual(candidate.format_type, "video")
        self.assertEqual(candidate.hook_type, "unknown")
        self.assertEqual(candidate.style_type, "unknown")
        self.assertEqual(candidate.cta_type, "none")

    def test_missing_facebook_post_id_fails_closed(self):
        from app.publication_ledger import record_published_content

        with self.assertRaisesRegex(RuntimeError, "Facebook post id"):
            record_published_content(
                Mock(),
                action="finance",
                endpoint="me/feed",
                request_data={"message": "Tài chính hôm nay"},
                response={},
            )

    def test_access_token_is_never_persisted_even_if_bad_caller_supplies_it(self):
        from app.publication_ledger import record_published_content

        with patch("app.publication_ledger.content_repository.record_candidate", return_value=9) as record:
            record_published_content(
                Mock(),
                action="finance",
                endpoint="me/feed",
                request_data={
                    "message": "Tài chính hôm nay",
                    "access_token": "must-not-leak",
                },
                response={"id": "finance-1"},
            )

        candidate = record.call_args.args[1]
        self.assertNotIn("must-not-leak", candidate.content_text)
        self.assertNotIn("must-not-leak", candidate.topic_text)
        self.assertNotIn("must-not-leak", candidate.topic_key)


if __name__ == "__main__":
    unittest.main()
