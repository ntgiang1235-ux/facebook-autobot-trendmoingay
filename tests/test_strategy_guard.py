import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from app.strategy_models import AdaptiveConfig


NOW = datetime(2026, 9, 1, 0, 32, tzinfo=timezone.utc)
RECENT_END = NOW - timedelta(hours=72)
RECENT_START = RECENT_END - timedelta(days=7)
PRIOR_START = RECENT_START - timedelta(days=7)


class StrategyGuardTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self._schema()

    def tearDown(self):
        self.conn.close()

    def execute(self, query, params=()):
        cur = self.conn.execute(query, params)
        self.conn.commit()
        return cur.fetchall()

    def _schema(self):
        self.conn.executescript(
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
        self.conn.commit()

    def _config(self, *, adaptive=True, current=2, last_good=1):
        self.conn.execute(
            "INSERT OR REPLACE INTO adaptive_config VALUES (1, ?, 1, 1, 0.20, 12, ?, ?)",
            (int(adaptive), current, last_good),
        )
        self.conn.commit()

    def _version(self, version, weights, *, reason="test"):
        config = AdaptiveConfig(
            current_strategy_version=version,
            last_good_strategy_version=1 if version != 1 else 1,
        )
        self.conn.execute(
            "INSERT INTO strategy_versions VALUES (?, ?, ?, ?, ?, ?)",
            (
                version,
                json.dumps(weights, sort_keys=True),
                json.dumps(config.to_dict(), sort_keys=True),
                (NOW - timedelta(days=1)).isoformat(),
                reason,
                int(version == 1),
            ),
        )
        self.conn.commit()

    def _stat(self, dimension, value, weight, status="active"):
        self.conn.execute(
            """
            INSERT INTO strategy_stats VALUES (?, ?, 8, 60, 60, 0.6, ?, NULL, ?, NULL, NULL, ?)
            """,
            (dimension, value, weight, status, NOW.isoformat()),
        )
        self.conn.commit()

    def _post(self, post_id, published_at, score=None, *, mode="exploit", version=1):
        self.conn.execute(
            "INSERT INTO content_posts VALUES (?, 'published', ?, ?, ?)",
            (post_id, published_at.isoformat(), mode, version),
        )
        if score is not None:
            self.conn.execute(
                "INSERT INTO content_metrics VALUES (?, 'final', ?)",
                (post_id, score),
            )
        self.conn.commit()

    def _cohort(self, prefix, start, scores, *, eligible_extra=0):
        for index, score in enumerate(scores):
            self._post(
                f"{prefix}-{index}",
                start + timedelta(hours=index + 1),
                score,
            )
        for index in range(eligible_extra):
            self._post(
                f"{prefix}-missing-{index}",
                start + timedelta(hours=20 + index),
                None,
            )

    def _healthy_base(self, recent_scores=None, prior_scores=None):
        self._config()
        self._version(1, {"category": {"post": 0.7, "finance": 0.3}})
        self._version(2, {"category": {"post": 0.4, "finance": 0.2, "fun": 0.4}})
        self._stat("category", "post", 0.4)
        self._stat("category", "finance", 0.2, "suspended")
        self._stat("category", "fun", 0.4)
        self._stat("hook_type", "retired_custom", 0.5, "retired")
        self._cohort("prior", PRIOR_START, prior_scores or [100, 100, 100, 100, 100])
        self._cohort("recent", RECENT_START, recent_scores or [90, 90, 90, 90, 90])

    def test_mature_windows_are_adjacent_and_exclude_manual_unversioned_and_too_new_posts(self):
        from app.strategy_guard import run_strategy_guard

        self._healthy_base(
            recent_scores=[90, 90, 90, 90, 90],
            prior_scores=[90, 90, 90, 90, 90],
        )
        self._post("too-new", RECENT_END + timedelta(minutes=1), 1)
        self._post("too-old", PRIOR_START - timedelta(minutes=1), 1)
        self._post("manual", RECENT_START + timedelta(hours=30), 1, mode="manual")
        self._post("unversioned", RECENT_START + timedelta(hours=31), 1, version=None)

        result = run_strategy_guard(self.execute, now=NOW)

        self.assertEqual(result.recent.eligible_count, 5)
        self.assertEqual(result.prior.eligible_count, 5)
        self.assertEqual(result.recent.final_count, 5)
        self.assertEqual(result.prior.final_count, 5)
        self.assertEqual(result.recent.average_score, 90.0)
        self.assertEqual(result.prior.average_score, 90.0)

    def test_fewer_than_five_final_samples_skips(self):
        from app.strategy_guard import run_strategy_guard

        self._config()
        self._version(1, {"category": {"post": 1.0}})
        self._version(2, {"category": {"post": 1.0}})
        self._cohort("prior", PRIOR_START, [80, 80, 80, 80])
        self._cohort("recent", RECENT_START, [40, 40, 40, 40])

        result = run_strategy_guard(self.execute, now=NOW)
        self.assertEqual(result.status, "insufficient_data")

    def test_metric_coverage_below_eighty_percent_skips_even_with_five_final_scores(self):
        from app.strategy_guard import run_strategy_guard

        self._config()
        self._version(1, {"category": {"post": 1.0}})
        self._version(2, {"category": {"post": 1.0}})
        self._cohort("prior", PRIOR_START, [90] * 5)
        self._cohort("recent", RECENT_START, [50] * 5, eligible_extra=2)

        result = run_strategy_guard(self.execute, now=NOW)
        self.assertEqual(result.status, "metric_degraded")
        self.assertLess(result.recent.coverage, 0.80)

    def test_exact_eighty_percent_coverage_is_accepted(self):
        from app.strategy_guard import run_strategy_guard

        self._config(current=1, last_good=1)
        self._version(1, {"category": {"post": 1.0}})
        self._cohort("prior", PRIOR_START, [90] * 8, eligible_extra=2)
        self._cohort("recent", RECENT_START, [90] * 8, eligible_extra=2)

        result = run_strategy_guard(self.execute, now=NOW)
        self.assertEqual(result.status, "stable")
        self.assertAlmostEqual(result.recent.coverage, 0.80)
        self.assertAlmostEqual(result.prior.coverage, 0.80)

    def test_exactly_twenty_percent_regression_does_not_rollback_and_promotes_current(self):
        from app.strategy_guard import run_strategy_guard

        self._healthy_base(
            recent_scores=[80, 80, 80, 80, 80],
            prior_scores=[100, 100, 100, 100, 100],
        )

        result = run_strategy_guard(self.execute, now=NOW)

        self.assertEqual(result.status, "promoted_last_good")
        config_row = self.conn.execute(
            "SELECT current_strategy_version, last_good_strategy_version FROM adaptive_config WHERE id = 1"
        ).fetchone()
        self.assertEqual(config_row, (2, 2))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM strategy_versions").fetchone()[0], 2)

    def test_regression_greater_than_twenty_percent_rolls_back_weights_and_appends_version(self):
        from app.strategy_guard import run_strategy_guard

        self._healthy_base(
            recent_scores=[79, 79, 79, 79, 79],
            prior_scores=[100, 100, 100, 100, 100],
        )

        result = run_strategy_guard(self.execute, now=NOW)

        self.assertEqual(result.status, "rolled_back")
        self.assertEqual(result.last_good_version, 1)
        self.assertEqual(result.rollback_version, 3)
        config_row = self.conn.execute(
            "SELECT current_strategy_version, last_good_strategy_version FROM adaptive_config WHERE id = 1"
        ).fetchone()
        self.assertEqual(config_row, (3, 1))
        weights = dict(
            self.conn.execute(
                "SELECT value, current_weight FROM strategy_stats WHERE dimension = 'category'"
            ).fetchall()
        )
        self.assertEqual(weights["post"], 0.7)
        self.assertEqual(weights["finance"], 0.0)
        self.assertEqual(weights["fun"], 0.0)
        retired = self.conn.execute(
            "SELECT current_weight FROM strategy_stats WHERE dimension = 'hook_type' AND value = 'retired_custom'"
        ).fetchone()[0]
        self.assertEqual(retired, 0.0)
        version = self.conn.execute(
            "SELECT reason FROM strategy_versions WHERE version_id = 3"
        ).fetchone()
        self.assertEqual(version, ("automatic rollback to v1",))

    def test_disabled_adaptive_guard_skips_without_state_change(self):
        from app.strategy_guard import run_strategy_guard

        self._config(adaptive=False, current=2, last_good=1)
        self._version(1, {"category": {"post": 1.0}})
        self._version(2, {"category": {"post": 1.0}})

        result = run_strategy_guard(self.execute, now=NOW)
        self.assertEqual(result.status, "disabled")
        self.assertEqual(
            self.conn.execute(
                "SELECT current_strategy_version, last_good_strategy_version FROM adaptive_config WHERE id = 1"
            ).fetchone(),
            (2, 1),
        )

    def test_regression_without_last_good_snapshot_skips_safely(self):
        from app.strategy_guard import run_strategy_guard

        self._config(current=2, last_good=None)
        self._version(2, {"category": {"post": 1.0}})
        self._cohort("prior", PRIOR_START, [100] * 5)
        self._cohort("recent", RECENT_START, [70] * 5)

        result = run_strategy_guard(self.execute, now=NOW)
        self.assertEqual(result.status, "no_last_good")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM strategy_versions").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
