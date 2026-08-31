import sqlite3
import unittest

from app import readiness


class ReadinessInvariantTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self._create_schema()
        self._seed_healthy_state()

    def tearDown(self):
        self.conn.close()

    def execute(self, query, params=()):
        return self.conn.execute(query, params).fetchall()

    def assert_check(self, checks, name, status):
        matches = [item for item in checks if item.name == name]
        self.assertEqual(len(matches), 1, f"expected one {name} check, got {matches}")
        self.assertEqual(matches[0].status, status, matches[0].detail)
        return matches[0]

    def _create_schema(self):
        self.conn.executescript(
            """
            CREATE TABLE job_runs (
                run_key TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                detail TEXT,
                scheduled_for TEXT,
                delay_minutes INTEGER
            );

            CREATE TABLE content_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                facebook_post_id TEXT,
                category TEXT NOT NULL,
                hook_type TEXT NOT NULL,
                style_type TEXT NOT NULL,
                cta_type TEXT NOT NULL,
                format_type TEXT NOT NULL,
                style_experiment_key TEXT,
                scheduled_for TEXT,
                published_at TEXT,
                strategy_mode TEXT NOT NULL,
                strategy_version INTEGER,
                status TEXT NOT NULL
            );

            CREATE TABLE content_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                facebook_post_id TEXT NOT NULL,
                score_kind TEXT NOT NULL,
                content_score REAL
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

            CREATE TABLE strategy_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                is_last_good INTEGER NOT NULL
            );

            CREATE TABLE daily_plan (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_date TEXT NOT NULL,
                slot_id TEXT NOT NULL,
                planned_for TEXT NOT NULL,
                action TEXT NOT NULL,
                category TEXT NOT NULL,
                strategy_mode TEXT NOT NULL,
                strategy_version INTEGER,
                status TEXT NOT NULL,
                claim_run_key TEXT,
                claimed_at TEXT,
                finished_at TEXT,
                detail TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(plan_date, slot_id)
            );
            """
        )

    def _seed_healthy_state(self):
        self.conn.execute(
            "INSERT INTO adaptive_config VALUES (1, 1, 1, 1, 0.20, 12, 2, 1)"
        )
        self.conn.executemany(
            """
            INSERT INTO strategy_versions (
                version_id, weights_json, config_json, created_at, reason, is_last_good
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    1,
                    '{"category":{"post":0.4}}',
                    '{"current_strategy_version":1,"last_good_strategy_version":1}',
                    "2026-08-01T00:00:00+00:00",
                    "proven",
                    1,
                ),
                (
                    2,
                    '{"category":{"post":0.4}}',
                    '{"current_strategy_version":2,"last_good_strategy_version":1}',
                    "2026-08-31T00:00:00+00:00",
                    "refresh",
                    0,
                ),
            ),
        )

        for value in ("post", "finance", "philosophy", "fun", "recipe", "video"):
            self._insert_stat("category", value, current_weight=1 / 6)
        for value in ("08:30", "09:00", "09:30", "10:00", "10:30", "11:00"):
            self._insert_stat("time_bucket", value, current_weight=1 / 6)
        self._insert_stat("format_type", "text", current_weight=1.0)
        self._insert_stat("hook_type", "question", current_weight=1.0)
        self._insert_stat("style_type", "conversational", current_weight=1.0)
        self._insert_stat("cta_type", "opinion_question", current_weight=1.0)

        for dimension, value in (
            ("hook", "question"),
            ("tone", "conversational"),
            ("cta", "opinion_question"),
        ):
            self.conn.execute(
                """
                INSERT INTO style_registry (
                    dimension, value, parent_value, status, created_at,
                    promoted_at, retired_at
                ) VALUES (?, ?, NULL, 'baseline', '2026-08-01T00:00:00+00:00', NULL, NULL)
                """,
                (dimension, value),
            )
        self.conn.commit()

    def _insert_stat(
        self,
        dimension,
        value,
        *,
        sample_count=5,
        weighted_score=60.0,
        recent_score=60.0,
        success_rate=0.8,
        current_weight=0.2,
        status="active",
        retest_after=None,
    ):
        self.conn.execute(
            """
            INSERT INTO strategy_stats (
                dimension, value, sample_count, weighted_score_14d,
                recent_score_7d, success_rate, current_weight, last_used_at,
                status, cooldown_until, retest_after, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?)
            """,
            (
                dimension,
                value,
                sample_count,
                weighted_score,
                recent_score,
                success_rate,
                current_weight,
                status,
                retest_after,
                "2026-08-31T00:00:00+00:00",
            ),
        )

    def test_aggregate_status_precedence(self):
        ready = readiness.ReadinessCheck("a", "ready", "ok")
        degraded = readiness.ReadinessCheck("b", "degraded", "warming up")
        failed = readiness.ReadinessCheck("c", "failed", "broken")
        self.assertEqual(readiness.aggregate_checks([ready]), "ready")
        self.assertEqual(readiness.aggregate_checks([ready, degraded]), "degraded")
        self.assertEqual(
            readiness.aggregate_checks([ready, degraded, failed]),
            "failed",
        )

    def test_healthy_core_state_has_no_failed_checks(self):
        checks = readiness.run_core_checks(self.execute)
        self.assertNotIn("failed", {item.status for item in checks})
        self.assertEqual(readiness.aggregate_checks(checks), "ready")

    def test_missing_required_table_is_failed(self):
        self.conn.execute("DROP TABLE content_metrics")
        self.assert_check(readiness.run_core_checks(self.execute), "schema", "failed")

    def test_missing_required_column_is_failed(self):
        self.conn.execute("ALTER TABLE content_posts RENAME TO content_posts_old")
        self.conn.execute(
            """
            CREATE TABLE content_posts (
                facebook_post_id TEXT, category TEXT, hook_type TEXT, style_type TEXT,
                cta_type TEXT, format_type TEXT, scheduled_for TEXT, published_at TEXT,
                strategy_mode TEXT, strategy_version INTEGER, status TEXT
            )
            """
        )
        check = self.assert_check(
            readiness.run_core_checks(self.execute),
            "schema",
            "failed",
        )
        self.assertIn("style_experiment_key", check.detail)

    def test_missing_real_config_row_is_failed(self):
        self.conn.execute("DELETE FROM adaptive_config")
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "adaptive_config",
            "failed",
        )

    def test_invalid_config_values_are_failed(self):
        self.conn.execute(
            "UPDATE adaptive_config SET exploration_rate = 1.5 WHERE id = 1"
        )
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "adaptive_config",
            "failed",
        )

    def test_dangling_current_pointer_is_failed(self):
        self.conn.execute(
            "UPDATE adaptive_config SET current_strategy_version = 999 WHERE id = 1"
        )
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "strategy_versions",
            "failed",
        )

    def test_dangling_last_good_pointer_is_failed(self):
        self.conn.execute(
            "UPDATE adaptive_config SET last_good_strategy_version = 999 WHERE id = 1"
        )
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "strategy_versions",
            "failed",
        )

    def test_last_good_cannot_be_newer_than_current(self):
        self.conn.execute(
            "UPDATE adaptive_config SET current_strategy_version = 1, last_good_strategy_version = 2 WHERE id = 1"
        )
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "strategy_versions",
            "failed",
        )

    def test_wrong_last_good_audit_flag_is_failed(self):
        self.conn.execute(
            "UPDATE strategy_versions SET is_last_good = CASE version_id WHEN 1 THEN 0 ELSE 1 END"
        )
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "strategy_versions",
            "failed",
        )

    def test_malformed_snapshot_json_is_failed(self):
        self.conn.execute(
            "UPDATE strategy_versions SET weights_json = 'not-json' WHERE version_id = 2"
        )
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "strategy_versions",
            "failed",
        )

    def test_current_snapshot_self_pointer_mismatch_is_failed(self):
        self.conn.execute(
            "UPDATE strategy_versions SET config_json = '{\"current_strategy_version\":1}' WHERE version_id = 2"
        )
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "strategy_versions",
            "failed",
        )

    def test_no_current_strategy_is_degraded_not_failed(self):
        self.conn.execute(
            "UPDATE adaptive_config SET current_strategy_version = NULL WHERE id = 1"
        )
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "strategy_versions",
            "degraded",
        )

    def test_no_last_good_strategy_is_degraded_not_failed(self):
        self.conn.execute(
            "UPDATE adaptive_config SET last_good_strategy_version = NULL WHERE id = 1"
        )
        self.conn.execute("UPDATE strategy_versions SET is_last_good = 0")
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "strategy_versions",
            "degraded",
        )

    def test_nonfinite_strategy_value_is_failed(self):
        self.conn.execute(
            "UPDATE strategy_stats SET current_weight = ? WHERE dimension = 'category' AND value = 'post'",
            (float("inf"),),
        )
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "strategy_stats",
            "failed",
        )

    def test_negative_strategy_weight_is_failed(self):
        self.conn.execute(
            "UPDATE strategy_stats SET current_weight = -0.1 WHERE dimension = 'category' AND value = 'post'"
        )
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "strategy_stats",
            "failed",
        )

    def test_suspended_positive_weight_is_failed(self):
        self.conn.execute(
            "UPDATE strategy_stats SET status = 'suspended', current_weight = 0.2 WHERE dimension = 'category' AND value = 'post'"
        )
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "strategy_stats",
            "failed",
        )

    def test_unknown_category_is_failed(self):
        self._insert_stat("category", "unknown_category", current_weight=0.2)
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "strategy_stats",
            "failed",
        )

    def test_unsafe_active_time_bucket_is_failed(self):
        self._insert_stat("time_bucket", "23:30", current_weight=0.2)
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "strategy_stats",
            "failed",
        )

    def test_unknown_strategy_dimension_is_failed(self):
        self._insert_stat("mystery", "x", current_weight=0.2)
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "strategy_stats",
            "failed",
        )

    def test_multiple_pending_experiments_are_failed(self):
        self.conn.execute(
            "INSERT INTO style_registry(dimension,value,parent_value,status,created_at) VALUES ('hook','question_alt','question','explore','2026-08-31T00:00:00+00:00')"
        )
        self.conn.execute(
            "INSERT INTO style_registry(dimension,value,parent_value,status,created_at) VALUES ('cta','opinion_alt','opinion_question','explore','2026-08-31T00:00:00+00:00')"
        )
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "style_registry",
            "failed",
        )

    def test_orphan_experiment_parent_is_failed(self):
        self.conn.execute(
            "INSERT INTO style_registry(dimension,value,parent_value,status,created_at) VALUES ('hook','question_alt','missing_parent','explore','2026-08-31T00:00:00+00:00')"
        )
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "style_registry",
            "failed",
        )

    def test_retired_experiment_parent_is_failed(self):
        self.conn.execute(
            "UPDATE style_registry SET status='retired', retired_at='2026-08-30T00:00:00+00:00' WHERE dimension='hook' AND value='question'"
        )
        self.conn.execute(
            "INSERT INTO style_registry(dimension,value,parent_value,status,created_at) VALUES ('hook','question_alt','question','explore','2026-08-31T00:00:00+00:00')"
        )
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "style_registry",
            "failed",
        )

    def test_baseline_row_cannot_have_parent(self):
        self.conn.execute(
            "UPDATE style_registry SET parent_value='parent' WHERE dimension='hook' AND value='question'"
        )
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "style_registry",
            "failed",
        )

    def test_retired_registry_value_with_positive_strategy_weight_is_failed(self):
        self.conn.execute(
            "INSERT INTO style_registry(dimension,value,parent_value,status,created_at,retired_at) VALUES ('hook','question_alt','question','retired','2026-08-01T00:00:00+00:00','2026-08-30T00:00:00+00:00')"
        )
        self._insert_stat("hook_type", "question_alt", current_weight=0.2)
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "style_registry",
            "failed",
        )

    def test_explore_registry_value_with_positive_strategy_weight_is_failed(self):
        self.conn.execute(
            "INSERT INTO style_registry(dimension,value,parent_value,status,created_at) VALUES ('hook','question_alt','question','explore','2026-08-31T00:00:00+00:00')"
        )
        self._insert_stat("hook_type", "question_alt", current_weight=0.2)
        self.assert_check(
            readiness.run_core_checks(self.execute),
            "style_registry",
            "failed",
        )


if __name__ == "__main__":
    unittest.main()
