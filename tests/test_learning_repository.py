import sqlite3
import unittest
from datetime import datetime, timedelta, timezone


class LearningRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            """
            CREATE TABLE content_posts (
                id INTEGER PRIMARY KEY,
                facebook_post_id TEXT,
                category TEXT NOT NULL,
                hook_type TEXT NOT NULL,
                style_type TEXT NOT NULL,
                cta_type TEXT NOT NULL,
                format_type TEXT NOT NULL,
                scheduled_for TEXT,
                published_at TEXT,
                strategy_mode TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE content_metrics (
                facebook_post_id TEXT NOT NULL,
                measured_at TEXT NOT NULL,
                content_score REAL NOT NULL,
                score_kind TEXT NOT NULL
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

    def _insert(self, post_id, *, mode, published_at, scheduled_for, score, kind="final"):
        self.conn.execute(
            """
            INSERT INTO content_posts (
                id, facebook_post_id, category, hook_type, style_type, cta_type,
                format_type, scheduled_for, published_at, strategy_mode, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'published')
            """,
            (
                post_id,
                f"fb-{post_id}",
                "finance",
                "question",
                "concise",
                "ask_comment",
                "text",
                scheduled_for,
                published_at,
                mode,
            ),
        )
        self.conn.execute(
            "INSERT INTO content_metrics (facebook_post_id, measured_at, content_score, score_kind) VALUES (?, ?, ?, ?)",
            (f"fb-{post_id}", published_at, score, kind),
        )
        self.conn.commit()

    def test_load_observations_excludes_manual_and_outside_14_day_window(self):
        from app.learning_repository import load_learning_observations

        now = datetime(2026, 9, 1, 1, 0, tzinfo=timezone.utc)
        adaptive_time = now - timedelta(days=2)
        manual_time = now - timedelta(days=1)
        old_time = now - timedelta(days=15)

        self._insert(
            1,
            mode="exploit",
            published_at=adaptive_time.isoformat(),
            scheduled_for="2026-08-30T04:00:00+00:00",
            score=82,
        )
        self._insert(
            2,
            mode="manual",
            published_at=manual_time.isoformat(),
            scheduled_for="2026-08-31T04:00:00+00:00",
            score=99,
        )
        self._insert(
            3,
            mode="explore",
            published_at=old_time.isoformat(),
            scheduled_for="2026-08-17T04:00:00+00:00",
            score=10,
        )

        observations = load_learning_observations(self.execute, now=now)

        self.assertEqual(len(observations), 1)
        item = observations[0]
        self.assertEqual(item.facebook_post_id, "fb-1")
        self.assertEqual(item.category, "finance")
        self.assertEqual(item.time_bucket, "11:00")
        self.assertEqual(item.hook_type, "question")
        self.assertEqual(item.style_type, "concise")
        self.assertEqual(item.cta_type, "ask_comment")
        self.assertEqual(item.format_type, "text")
        self.assertEqual(item.score, 82.0)
        self.assertEqual(item.score_kind, "final")

    def test_unknown_style_metadata_is_preserved_in_observation_but_can_be_filtered_by_updater(self):
        from app.learning_repository import load_learning_observations

        now = datetime(2026, 9, 1, 1, 0, tzinfo=timezone.utc)
        published = now - timedelta(days=1)
        self.conn.execute(
            """
            INSERT INTO content_posts (
                id, facebook_post_id, category, hook_type, style_type, cta_type,
                format_type, scheduled_for, published_at, strategy_mode, status
            ) VALUES (1, 'fb-1', 'post', 'unknown', 'unknown', 'none', 'photo', ?, ?, 'baseline', 'published')
            """,
            ("2026-08-31T01:30:00+00:00", published.isoformat()),
        )
        self.conn.execute(
            "INSERT INTO content_metrics VALUES ('fb-1', ?, 55, 'early')",
            (published.isoformat(),),
        )
        self.conn.commit()

        [item] = load_learning_observations(self.execute, now=now)
        self.assertEqual(item.time_bucket, "08:30")
        self.assertEqual(item.hook_type, "unknown")
        self.assertEqual(item.style_type, "unknown")
        self.assertEqual(item.cta_type, "none")
        self.assertEqual(item.format_type, "photo")


if __name__ == "__main__":
    unittest.main()
