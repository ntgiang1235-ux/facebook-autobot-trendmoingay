import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from app.readiness import ReadinessCheck, ReadinessResult
from app.readiness_policy import apply_bootstrap_policy
from app.strategy_guard import run_strategy_guard
from app.strategy_models import AdaptiveConfig


NOW = datetime(2026, 9, 1, 0, 32, tzinfo=timezone.utc)
RECENT_END = NOW - timedelta(hours=72)
RECENT_START = RECENT_END - timedelta(days=7)
PRIOR_START = RECENT_START - timedelta(days=7)


class FinalReadinessClosureTests(unittest.TestCase):
    def test_bootstrap_learning_and_missing_last_good_become_ready_but_transparent(self):
        raw = ReadinessResult(
            "degraded",
            (
                ReadinessCheck("schema", "ready", "ok"),
                ReadinessCheck(
                    "strategy_versions",
                    "degraded",
                    "current=v2; no proven last-good rollback target yet",
                ),
                ReadinessCheck(
                    "learning",
                    "degraded",
                    "insufficient mature learning for: category, time_bucket",
                ),
                ReadinessCheck("liveness", "ready", "healthy"),
            ),
        )

        result = apply_bootstrap_policy(raw)

        self.assertEqual(result.status, "ready")
        learning = next(item for item in result.checks if item.name == "learning")
        versions = next(item for item in result.checks if item.name == "strategy_versions")
        self.assertEqual(learning.status, "ready")
        self.assertIn("bootstrap-safe", learning.detail)
        self.assertIn("category, time_bucket", learning.detail)
        self.assertEqual(versions.status, "ready")
        self.assertIn("bootstrap-safe", versions.detail)
        self.assertIn("no proven last-good", versions.detail)

    def test_mature_learning_does_not_hide_missing_last_good(self):
        raw = ReadinessResult(
            "degraded",
            (
                ReadinessCheck(
                    "strategy_versions",
                    "degraded",
                    "current=v2; no proven last-good rollback target yet",
                ),
                ReadinessCheck(
                    "learning",
                    "ready",
                    "category and time-bucket learning meet planner maturity thresholds",
                ),
            ),
        )

        result = apply_bootstrap_policy(raw)

        self.assertEqual(result.status, "degraded")
        versions = next(item for item in result.checks if item.name == "strategy_versions")
        self.assertEqual(versions.status, "degraded")

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
