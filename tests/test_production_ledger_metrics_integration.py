import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from app.metrics_repository import due_posts
from app.publication_context import PublicationContext
from app.publication_ledger import record_published_content


class ProductionLedgerMetricsIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            """
            CREATE TABLE content_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_key TEXT,
                facebook_post_id TEXT,
                action TEXT,
                category TEXT NOT NULL,
                topic_key TEXT NOT NULL,
                topic_text TEXT NOT NULL,
                source_url TEXT,
                source_title TEXT,
                content_text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                hook_type TEXT,
                style_type TEXT,
                cta_type TEXT,
                format_type TEXT,
                scheduled_for TEXT,
                published_at TEXT,
                strategy_mode TEXT,
                quality_score REAL,
                duplicate_score REAL,
                strategy_version INTEGER,
                status TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE content_metrics (
                facebook_post_id TEXT NOT NULL,
                score_kind TEXT NOT NULL,
                UNIQUE(facebook_post_id, score_kind)
            )
            """
        )

    def tearDown(self):
        self.conn.close()

    def execute(self, query, params=()):
        cursor = self.conn.execute(query, params)
        rows = cursor.fetchall() if cursor.description is not None else []
        self.conn.commit()
        return rows

    def test_dispatch_publish_is_visible_to_24h_metrics_collector(self):
        published_at = datetime(2026, 8, 31, 1, 30, tzinfo=timezone.utc)
        context = PublicationContext(
            run_key="dispatch-acceptance",
            category="finance",
            scheduled_for=published_at.isoformat(),
            strategy_mode="exploit",
            strategy_version=14,
        )

        content_id = record_published_content(
            self.execute,
            action="finance",
            endpoint="me/feed",
            request_data={
                "message": "Thị trường tài chính hôm nay có ba điểm đáng chú ý.",
                "link": "https://example.com/finance-story",
            },
            response={"id": "page_finance_123"},
            context=context,
            now=published_at,
        )

        stored = self.execute(
            """
            SELECT facebook_post_id, scheduled_for, published_at, strategy_mode,
                   strategy_version, status
            FROM content_posts
            WHERE id = ?
            """,
            (content_id,),
        )[0]
        self.assertEqual(
            stored,
            (
                "page_finance_123",
                published_at.isoformat(),
                published_at.isoformat(),
                "exploit",
                14,
                "published",
            ),
        )

        self.assertEqual(due_posts(self.execute, (published_at + timedelta(hours=23, minutes=59)).isoformat()), [])
        due = due_posts(self.execute, (published_at + timedelta(hours=24, minutes=1)).isoformat())
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].content_id, content_id)
        self.assertEqual(due[0].facebook_post_id, "page_finance_123")
        self.assertEqual(due[0].score_kind, "early")


if __name__ == "__main__":
    unittest.main()
