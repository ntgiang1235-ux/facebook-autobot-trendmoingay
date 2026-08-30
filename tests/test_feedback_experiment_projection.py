import sqlite3
import unittest
from datetime import datetime, timedelta, timezone


class FeedbackExperimentProjectionTests(unittest.TestCase):
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
                style_experiment_key TEXT,
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
            CREATE TABLE style_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dimension TEXT NOT NULL,
                value TEXT NOT NULL,
                parent_value TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                promoted_at TEXT,
                retired_at TEXT,
                UNIQUE(dimension, value)
            );
            """
        )
        self.conn.execute(
            "INSERT INTO adaptive_config VALUES (1, 1, 1, 1, 0.20, 12, NULL, NULL)"
        )
        self.conn.execute(
            """
            INSERT INTO style_registry (
                dimension, value, parent_value, status, created_at
            ) VALUES ('tone', 'witty_short_punchline', 'witty', 'explore', '2026-08-20T00:00:00+00:00')
            """
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def execute(self, query, params=()):
        cursor = self.conn.execute(query, params)
        rows = cursor.fetchall() if cursor.description is not None else []
        self.conn.commit()
        return rows

    def _experiment_post(self, now, index, key="tone:witty_short_punchline"):
        published = now - timedelta(days=index + 1)
        post_id = f"exp-{index}"
        self.conn.execute(
            """
            INSERT INTO content_posts (
                facebook_post_id, category, hook_type, style_type, cta_type,
                format_type, style_experiment_key, scheduled_for, published_at,
                strategy_mode, status
            ) VALUES (?, 'fun', 'question', 'witty', 'choose_side', 'text', ?, ?, ?, 'explore', 'published')
            """,
            (
                post_id,
                key,
                published.replace(hour=4, minute=0).isoformat(),
                published.isoformat(),
            ),
        )
        self.conn.execute(
            """
            INSERT INTO content_metrics (
                facebook_post_id, measured_at, content_score, score_kind
            ) VALUES (?, ?, 82, 'final')
            """,
            (post_id, (published + timedelta(hours=72)).isoformat()),
        )

    def test_pending_experiment_gets_own_stat_but_cannot_exploit_before_promotion(self):
        from app.feedback_loop import refresh_strategy

        now = datetime(2026, 9, 1, 0, 30, tzinfo=timezone.utc)
        for index in range(5):
            self._experiment_post(now, index)
        self.conn.commit()

        refresh_strategy(self.execute, now=now)

        [projected] = self.execute(
            """
            SELECT sample_count, weighted_score_14d, status, current_weight
            FROM strategy_stats
            WHERE dimension='style_type' AND value='witty_short_punchline'
            """
        )
        [observed] = self.execute(
            """
            SELECT sample_count, weighted_score_14d
            FROM strategy_stats
            WHERE dimension='style_type' AND value='witty'
            """
        )
        self.assertEqual(projected[0], 5)
        self.assertAlmostEqual(projected[1], 82.0, places=6)
        self.assertEqual(projected[2], "insufficient_data")
        self.assertEqual(projected[3], 0.0)
        self.assertEqual(observed[0], 5)
        self.assertAlmostEqual(observed[1], 82.0, places=6)

    def test_promoted_experiment_becomes_active_strategy_on_next_refresh(self):
        from app.feedback_loop import refresh_strategy

        now = datetime(2026, 9, 1, 0, 30, tzinfo=timezone.utc)
        for index in range(5):
            self._experiment_post(now, index)
        self.conn.commit()

        refresh_strategy(self.execute, now=now)
        self.conn.execute(
            "UPDATE style_registry SET status='active' WHERE dimension='tone' AND value='witty_short_punchline'"
        )
        self.conn.commit()

        refresh_strategy(self.execute, now=now + timedelta(days=1))

        [projected] = self.execute(
            """
            SELECT sample_count, status, current_weight
            FROM strategy_stats
            WHERE dimension='style_type' AND value='witty_short_punchline'
            """
        )
        self.assertEqual(projected[0], 5)
        self.assertEqual(projected[1], "active")
        self.assertGreater(projected[2], 0.0)

    def test_malformed_experiment_key_is_not_projected(self):
        from app.feedback_loop import refresh_strategy

        now = datetime(2026, 9, 1, 0, 30, tzinfo=timezone.utc)
        for index in range(5):
            self._experiment_post(now, index, key="bad-key-without-dimension")
        self.conn.commit()

        refresh_strategy(self.execute, now=now)

        custom = self.execute(
            "SELECT value FROM strategy_stats WHERE value='bad-key-without-dimension'"
        )
        self.assertEqual(custom, [])

    def test_style_registry_read_failure_is_not_hidden(self):
        from app.feedback_loop import _style_registry_statuses

        def fail_registry(_query, _params=()):
            raise RuntimeError("style registry database unavailable")

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            _style_registry_statuses(fail_registry)


if __name__ == "__main__":
    unittest.main()
