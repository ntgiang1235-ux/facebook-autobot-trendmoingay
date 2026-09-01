import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from app import readiness
from app.strategy_guard import run_strategy_guard
from app.strategy_models import AdaptiveConfig


NOW = datetime(2026, 9, 1, 0, 32, tzinfo=timezone.utc)
RECENT_END = NOW - timedelta(hours=72)
RECENT_START = RECENT_END - timedelta(days=7)
PRIOR_START = RECENT_START - timedelta(days=7)


class FinalReadinessClosureTests(unittest.TestCase):
    def test_bootstrap_learning_is_ready_and_transparent(self):
        config = AdaptiveConfig(
            adaptive_enabled=True,
            auto_schedule_enabled=True,
            current_strategy_version=2,
        )
        stats = []

        check = readiness._learning_check(config, stats)

        self.assertEqual(check.status, "ready")
        self.assertIn("bootstrap-safe", check.detail)
        self.assertIn("category", check.detail)
        self.assertIn("time_bucket", check.detail)

    def test_missing_last_good_is_ready_only_while_learning_is_bootstrap_safe(self):
        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(
                """
                CREATE TABLE strategy_versions (
                    version_id INTEGER PRIMARY KEY,
                    weights_json TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    is_last_good INTEGER NOT NULL
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
                    updated_at TEXT NOT NULL
                );
                """
            )
            config = AdaptiveConfig(
                adaptive_enabled=True,
                auto_schedule_enabled=True,
                current_strategy_version=2,
                last_good_strategy_version=None,
            )
            conn.execute(
                "INSERT INTO strategy_versions VALUES (?, ?, ?, ?, ?, ?)",
                (
                    2,
                    json.dumps({"category": {"post": 1.0}}),
                    json.dumps(config.to_dict()),
                    NOW.isoformat(),
                    "refresh",
                    0,
                ),
            )
            for dimension, value in (("category", "post"), ("time_bucket", "08:30")):
                conn.execute(
                    "INSERT INTO strategy_stats VALUES (?, ?, 1, 50, 50, 0.5, 1, NULL, 'insufficient_data', NULL, NULL, ?)",
                    (dimension, value, NOW.isoformat()),
                )
            conn.commit()

            def execute(query, params=()):
                return conn.execute(query, params).fetchall()

            check = readiness._strategy_versions_check(execute, config)

            self.assertEqual(check.status, "ready")
            self.assertIn("bootstrap-safe", check.detail)
            self.assertIn("no proven last-good", check.detail)
        finally:
            conn.close()

    def test_healthy_guard_promotion_synchronizes_canonical_last_good_flag(self):
        conn = sqlite3.connect(":memory:")
        try:
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
            for version in (1, 2):
                cfg = AdaptiveConfig(
                    current_strategy_version=version,
                    last_good_strategy_version=1,
                )
                conn.execute(
                    "INSERT INTO strategy_versions VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        version,
                        json.dumps({"category": {"post": 1.0}}),
                        json.dumps(cfg.to_dict()),
                        (NOW - timedelta(days=1)).isoformat(),
                        "test",
                        int(version == 1),
                    ),
                )

            for prefix, start, score in (
                ("prior", PRIOR_START, 100.0),
                ("recent", RECENT_START, 90.0),
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

            def execute(query, params=()):
                cursor = conn.execute(query, params)
                conn.commit()
                return cursor.fetchall()

            result = run_strategy_guard(execute, now=NOW)

            self.assertEqual(result.status, "promoted_last_good")
            flags = conn.execute(
                "SELECT version_id, is_last_good FROM strategy_versions ORDER BY version_id"
            ).fetchall()
            self.assertEqual(flags, [(1, 0), (2, 1)])
            pointer = conn.execute(
                "SELECT last_good_strategy_version FROM adaptive_config WHERE id = 1"
            ).fetchone()[0]
            self.assertEqual(pointer, 2)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
