import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from app.strategy_models import AdaptiveConfig


class AdaptiveFeedbackLoopTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(
            """
            CREATE TABLE content_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                facebook_post_id TEXT,
                category TEXT NOT NULL,
                hook_type TEXT NOT NULL DEFAULT 'unknown',
                style_type TEXT NOT NULL DEFAULT 'unknown',
                cta_type TEXT NOT NULL DEFAULT 'none',
                format_type TEXT NOT NULL DEFAULT 'text',
                scheduled_for TEXT,
                published_at TEXT,
                strategy_mode TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE content_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                facebook_post_id TEXT NOT NULL,
                measured_at TEXT NOT NULL,
                content_score REAL NOT NULL,
                score_kind TEXT NOT NULL
            );
            CREATE TABLE strategy_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dimension TEXT NOT NULL,
                value TEXT NOT NULL,
                sample_count INTEGER NOT NULL DEFAULT 0,
                weighted_score_14d REAL NOT NULL DEFAULT 50.0,
                recent_score_7d REAL NOT NULL DEFAULT 50.0,
                success_rate REAL NOT NULL DEFAULT 0.0,
                current_weight REAL NOT NULL DEFAULT 1.0,
                last_used_at TEXT,
                status TEXT NOT NULL DEFAULT 'insufficient_data',
                cooldown_until TEXT,
                retest_after TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(dimension, value)
            );
            CREATE TABLE adaptive_config (
                id INTEGER PRIMARY KEY,
                adaptive_enabled INTEGER NOT NULL,
                auto_schedule_enabled INTEGER NOT NULL,
                auto_suspend_enabled INTEGER NOT NULL,
                exploration_rate REAL NOT NULL,
                baseline_daily_volume INTEGER NOT NULL,
                current_strategy_version INTEGER,
                last_good_strategy_version INTEGER
            );
            CREATE TABLE strategy_versions (
                version_id INTEGER PRIMARY KEY,
                weights_json TEXT NOT NULL,
                config_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                reason TEXT NOT NULL,
                is_last_good INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self.conn.execute(
            "INSERT INTO adaptive_config VALUES (1, 1, 1, 1, 0.20, 12, NULL, NULL)"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def execute(self, query, params=()):
        cursor = self.conn.execute(query, params)
        rows = cursor.fetchall() if cursor.description is not None else []
        self.conn.commit()
        return rows

    def _post(self, category, score, published_at, *, index, mode="exploit", hhmm_utc="04:00"):
        hour, minute = (int(part) for part in hhmm_utc.split(":", 1))
        scheduled = published_at.replace(hour=hour, minute=minute, second=0, microsecond=0)
        post_id = f"{category}-{index}"
        self.conn.execute(
            """
            INSERT INTO content_posts (
                facebook_post_id, category, hook_type, style_type, cta_type,
                format_type, scheduled_for, published_at, strategy_mode, status
            ) VALUES (?, ?, 'question', 'concise', 'ask_comment', 'text', ?, ?, ?, 'published')
            """,
            (post_id, category, scheduled.isoformat(), published_at.isoformat(), mode),
        )
        self.conn.execute(
            "INSERT INTO content_metrics (facebook_post_id, measured_at, content_score, score_kind) VALUES (?, ?, ?, 'final')",
            (post_id, (published_at + timedelta(hours=72)).isoformat(), score),
        )

    def test_refresh_updates_real_stats_bounded_weights_and_creates_version(self):
        from app.feedback_loop import refresh_strategy

        now = datetime(2026, 9, 1, 0, 30, tzinfo=timezone.utc)
        for index in range(5):
            published = now - timedelta(days=index + 1)
            self._post("finance", 80, published, index=index)
            self._post("post", 40, published, index=index)
            self._post("video", 60, published, index=index)
        # This extreme manual result must not influence adaptive learning.
        self._post("post", 100, now - timedelta(days=1), index=99, mode="manual")
        self.conn.commit()

        result = refresh_strategy(self.execute, now=now)

        self.assertEqual(result.version_id, 1)
        self.assertGreaterEqual(result.observation_count, 15)
        category_rows = self.execute(
            "SELECT value, sample_count, weighted_score_14d, current_weight, status FROM strategy_stats WHERE dimension='category' ORDER BY value"
        )
        by_value = {row[0]: row for row in category_rows}
        self.assertEqual(by_value["finance"][1], 5)
        self.assertAlmostEqual(by_value["finance"][2], 80.0, places=6)
        self.assertAlmostEqual(by_value["post"][2], 40.0, places=6)
        self.assertGreater(by_value["finance"][3], by_value["video"][3])
        self.assertGreater(by_value["video"][3], by_value["post"][3])
        self.assertAlmostEqual(sum(row[3] for row in category_rows), 1.0, places=6)
        # No category may move more than ±20% from the equal first-run seed.
        self.assertTrue(all((1 / 3) * 0.80 - 1e-9 <= row[3] <= (1 / 3) * 1.20 + 1e-9 for row in category_rows))

        [config_row] = self.execute(
            "SELECT current_strategy_version, last_good_strategy_version FROM adaptive_config WHERE id=1"
        )
        self.assertEqual(config_row, (1, 1))
        [version_row] = self.execute(
            "SELECT version_id, reason, is_last_good FROM strategy_versions"
        )
        self.assertEqual(version_row[0], 1)
        self.assertIn("14-day", version_row[1])
        self.assertEqual(version_row[2], 1)

    def test_persistently_weak_category_is_suspended_and_gets_retest_date(self):
        from app.feedback_loop import refresh_strategy

        now = datetime(2026, 9, 1, 0, 30, tzinfo=timezone.utc)
        for index in range(5):
            published = now - timedelta(days=index + 1)
            self._post("finance", 80, published, index=index)
            self._post("post", 70, published, index=index)
            self._post("recipe", 20, published, index=index)
        self.conn.commit()

        refresh_strategy(self.execute, now=now)

        [recipe] = self.execute(
            "SELECT status, current_weight, retest_after FROM strategy_stats WHERE dimension='category' AND value='recipe'"
        )
        self.assertEqual(recipe[0], "suspended")
        self.assertEqual(recipe[1], 0.0)
        self.assertEqual(datetime.fromisoformat(recipe[2]), now + timedelta(days=7))

    def test_unknown_hook_and_style_values_are_not_promoted_into_strategy_stats(self):
        from app.feedback_loop import refresh_strategy

        now = datetime(2026, 9, 1, 0, 30, tzinfo=timezone.utc)
        published = now - timedelta(days=3)
        self.conn.execute(
            """
            INSERT INTO content_posts (
                facebook_post_id, category, hook_type, style_type, cta_type,
                format_type, scheduled_for, published_at, strategy_mode, status
            ) VALUES ('legacy-1', 'post', 'unknown', 'unknown', 'none', 'photo', ?, ?, 'baseline', 'published')
            """,
            ("2026-08-29T01:30:00+00:00", published.isoformat()),
        )
        self.conn.execute(
            "INSERT INTO content_metrics (facebook_post_id, measured_at, content_score, score_kind) VALUES ('legacy-1', ?, 60, 'final')",
            ((published + timedelta(hours=72)).isoformat(),),
        )
        self.conn.commit()

        refresh_strategy(self.execute, now=now)

        hook_values = self.execute("SELECT value FROM strategy_stats WHERE dimension='hook_type'")
        style_values = self.execute("SELECT value FROM strategy_stats WHERE dimension='style_type'")
        format_values = self.execute("SELECT value FROM strategy_stats WHERE dimension='format_type'")
        self.assertEqual(hook_values, [])
        self.assertEqual(style_values, [])
        self.assertEqual(format_values, [("photo",)])


if __name__ == "__main__":
    unittest.main()
