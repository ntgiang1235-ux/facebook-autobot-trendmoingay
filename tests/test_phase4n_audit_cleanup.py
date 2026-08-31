import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from app.strategy_models import AdaptiveConfig


class Phase4NAuditCleanupTests(unittest.TestCase):
    def _reporting_connection(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE daily_plan (
                plan_date TEXT,
                status TEXT
            );
            CREATE TABLE content_posts (
                facebook_post_id TEXT,
                category TEXT,
                published_at TEXT
            );
            CREATE TABLE content_metrics (
                facebook_post_id TEXT,
                score_kind TEXT,
                content_score REAL,
                reach INTEGER,
                impressions INTEGER
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
            CREATE TABLE strategy_stats (
                dimension TEXT NOT NULL,
                value TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                weighted_score_14d REAL NOT NULL,
                recent_score_7d REAL NOT NULL,
                success_rate REAL NOT NULL,
                current_weight REAL NOT NULL,
                last_used_at TEXT,
                status TEXT NOT NULL,
                cooldown_until TEXT,
                retest_after TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(dimension, value)
            );
            """
        )
        conn.execute(
            "INSERT INTO adaptive_config VALUES (1, 1, 1, 1, 0.20, 12, 7, 6)"
        )
        for dimension, value, score in (
            ("category", "finance", 81.0),
            ("time_bucket", "20:00", 79.0),
            ("hook_type", "question", 78.0),
            ("style_type", "conversational", 77.0),
            ("cta_type", "opinion_question", 76.0),
        ):
            conn.execute(
                """
                INSERT INTO strategy_stats VALUES (
                    ?, ?, 8, ?, ?, 0.75, 0.20, NULL, 'active', NULL, NULL,
                    '2026-08-31T00:00:00+00:00'
                )
                """,
                (dimension, value, score, score),
            )
        conn.commit()
        return conn

    @staticmethod
    def _execute(conn):
        def execute(query, params=()):
            cur = conn.execute(query, params)
            conn.commit()
            return cur.fetchall()

        return execute

    def test_weekly_loader_uses_canonical_hook_and_style_dimensions(self):
        from app.reporting import load_weekly_report

        conn = self._reporting_connection()
        try:
            data = load_weekly_report(self._execute(conn), "2026-08-31")
        finally:
            conn.close()

        self.assertEqual(tuple(item.value for item in data.hooks), ("question",))
        self.assertEqual(
            tuple(item.value for item in data.styles),
            ("conversational",),
        )

    def test_weekly_report_exposes_learned_cta_dimension(self):
        from app.reporting import build_weekly_report, load_weekly_report

        conn = self._reporting_connection()
        try:
            data = load_weekly_report(self._execute(conn), "2026-08-31")
        finally:
            conn.close()

        self.assertTrue(hasattr(data, "ctas"), "weekly report data must expose CTA ranking")
        self.assertEqual(
            tuple(item.value for item in data.ctas),
            ("opinion_question",),
        )
        self.assertIn("CTA: opinion_question 76.0", build_weekly_report(data, "2026-08-31"))

    def test_promoting_current_strategy_synchronizes_last_good_audit_flags(self):
        from app.strategy_guard import run_strategy_guard

        now = datetime(2026, 9, 1, 0, 32, tzinfo=timezone.utc)
        recent_end = now - timedelta(hours=72)
        recent_start = recent_end - timedelta(days=7)
        prior_start = recent_start - timedelta(days=7)

        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE content_posts (
                facebook_post_id TEXT,
                status TEXT,
                published_at TEXT,
                strategy_mode TEXT,
                strategy_version INTEGER
            );
            CREATE TABLE content_metrics (
                facebook_post_id TEXT,
                score_kind TEXT,
                content_score REAL
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
            CREATE TABLE strategy_stats (
                dimension TEXT NOT NULL,
                value TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                weighted_score_14d REAL NOT NULL,
                recent_score_7d REAL NOT NULL,
                success_rate REAL NOT NULL,
                current_weight REAL NOT NULL,
                last_used_at TEXT,
                status TEXT NOT NULL,
                cooldown_until TEXT,
                retest_after TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(dimension, value)
            );
            """
        )
        conn.execute(
            "INSERT INTO adaptive_config VALUES (1, 1, 1, 1, 0.20, 12, 2, 1)"
        )
        for version, is_last_good in ((1, 1), (2, 0)):
            config = AdaptiveConfig(
                current_strategy_version=version,
                last_good_strategy_version=1,
            )
            conn.execute(
                "INSERT INTO strategy_versions VALUES (?, ?, ?, ?, ?, ?)",
                (
                    version,
                    json.dumps({"category": {"post": 1.0}}, sort_keys=True),
                    json.dumps(config.to_dict(), sort_keys=True),
                    now.isoformat(),
                    "test",
                    is_last_good,
                ),
            )
        for prefix, start, score in (
            ("prior", prior_start, 100.0),
            ("recent", recent_start, 90.0),
        ):
            for index in range(5):
                post_id = f"{prefix}-{index}"
                conn.execute(
                    "INSERT INTO content_posts VALUES (?, 'published', ?, 'exploit', 2)",
                    (post_id, (start + timedelta(hours=index + 1)).isoformat()),
                )
                conn.execute(
                    "INSERT INTO content_metrics VALUES (?, 'final', ?)",
                    (post_id, score),
                )
        conn.commit()

        try:
            result = run_strategy_guard(self._execute(conn), now=now)
            flags = conn.execute(
                "SELECT version_id, is_last_good FROM strategy_versions ORDER BY version_id"
            ).fetchall()
        finally:
            conn.close()

        self.assertEqual(result.status, "promoted_last_good")
        self.assertEqual(flags, [(1, 0), (2, 1)])


if __name__ == "__main__":
    unittest.main()
