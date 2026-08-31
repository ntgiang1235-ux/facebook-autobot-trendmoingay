import sqlite3
import unittest
from datetime import datetime, timezone

from app import readiness
from app.plan_repository import DailyPlanSlot


class ReadinessShadowPlanTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.now = datetime(2026, 8, 31, 2, 0, tzinfo=timezone.utc)
        self._create_schema()
        self._seed_healthy_state()

    def tearDown(self):
        self.conn.close()

    def execute(self, query, params=()):
        return self.conn.execute(query, params).fetchall()

    def get_check(self, result, name):
        matches = [item for item in result.checks if item.name == name]
        self.assertEqual(len(matches), 1, f"expected one {name} check, got {matches}")
        return matches[0]

    def _create_schema(self):
        self.conn.executescript(
            """
            CREATE TABLE job_runs (
                run_key TEXT PRIMARY KEY, action TEXT, status TEXT, started_at TEXT,
                finished_at TEXT, detail TEXT, scheduled_for TEXT, delay_minutes INTEGER
            );
            CREATE TABLE content_posts (
                facebook_post_id TEXT, category TEXT, hook_type TEXT, style_type TEXT,
                cta_type TEXT, format_type TEXT, style_experiment_key TEXT,
                scheduled_for TEXT, published_at TEXT, strategy_mode TEXT,
                strategy_version INTEGER, status TEXT
            );
            CREATE TABLE content_metrics (
                facebook_post_id TEXT, score_kind TEXT, content_score REAL
            );
            CREATE TABLE style_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT, dimension TEXT, value TEXT,
                parent_value TEXT, status TEXT, created_at TEXT, promoted_at TEXT,
                retired_at TEXT, UNIQUE(dimension, value)
            );
            CREATE TABLE strategy_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT, dimension TEXT, value TEXT,
                sample_count INTEGER, weighted_score_14d REAL, recent_score_7d REAL,
                success_rate REAL, current_weight REAL, last_used_at TEXT, status TEXT,
                cooldown_until TEXT, retest_after TEXT, updated_at TEXT,
                UNIQUE(dimension, value)
            );
            CREATE TABLE adaptive_config (
                id INTEGER PRIMARY KEY, adaptive_enabled INTEGER,
                auto_schedule_enabled INTEGER, auto_suspend_enabled INTEGER,
                exploration_rate REAL, baseline_daily_volume INTEGER,
                current_strategy_version INTEGER, last_good_strategy_version INTEGER
            );
            CREATE TABLE strategy_versions (
                version_id INTEGER PRIMARY KEY, weights_json TEXT, config_json TEXT,
                created_at TEXT, reason TEXT, is_last_good INTEGER
            );
            CREATE TABLE daily_plan (
                plan_date TEXT, slot_id TEXT, planned_for TEXT, action TEXT, category TEXT,
                strategy_mode TEXT, strategy_version INTEGER, status TEXT,
                claim_run_key TEXT, claimed_at TEXT, finished_at TEXT, detail TEXT,
                created_at TEXT, UNIQUE(plan_date, slot_id)
            );
            """
        )

    def _seed_healthy_state(self):
        self.conn.execute(
            "INSERT INTO adaptive_config VALUES (1, 1, 1, 1, 0.20, 12, 2, 1)"
        )
        self.conn.executemany(
            "INSERT INTO strategy_versions VALUES (?, ?, ?, ?, ?, ?)",
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
            self._insert_stat("category", value, 1 / 6)
        for value in ("08:30", "09:00", "09:30", "10:00", "10:30", "11:00"):
            self._insert_stat("time_bucket", value, 1 / 6)
        self._insert_stat("format_type", "text", 1.0)
        self._insert_stat("hook_type", "question", 1.0)
        self._insert_stat("style_type", "conversational", 1.0)
        self._insert_stat("cta_type", "opinion_question", 1.0)
        for dimension, value in (
            ("hook", "question"),
            ("tone", "conversational"),
            ("cta", "opinion_question"),
        ):
            self.conn.execute(
                "INSERT INTO style_registry(dimension,value,parent_value,status,created_at) VALUES (?, ?, NULL, 'baseline', '2026-08-01T00:00:00+00:00')",
                (dimension, value),
            )
        self.conn.execute(
            """
            INSERT INTO daily_plan VALUES (
                '2026-08-31', '0830-post-01', '2026-08-31T01:30:00+00:00',
                'post', 'post', 'baseline', 2, 'planned', NULL, NULL, NULL, '',
                '2026-08-31T00:47:00+00:00'
            )
            """
        )
        self.conn.execute(
            """
            INSERT INTO job_runs VALUES (
                'dispatch-recent', 'dispatch', 'skipped',
                '2026-08-31T01:50:00+00:00', '2026-08-31T01:50:10+00:00',
                'no due plan slot', NULL, NULL
            )
            """
        )
        self.conn.commit()

    def _insert_stat(self, dimension, value, current_weight):
        self.conn.execute(
            """
            INSERT INTO strategy_stats (
                dimension, value, sample_count, weighted_score_14d,
                recent_score_7d, success_rate, current_weight, last_used_at,
                status, cooldown_until, retest_after, updated_at
            ) VALUES (?, ?, 5, 60.0, 60.0, 0.8, ?, NULL, 'active', NULL, NULL, ?)
            """,
            (dimension, value, current_weight, "2026-08-31T00:00:00+00:00"),
        )

    def _slot(self, *, slot_id="0830-post-01", planned_for="2026-09-01T01:30:00+00:00"):
        return DailyPlanSlot(
            plan_date="2026-09-01",
            slot_id=slot_id,
            planned_for=planned_for,
            action="post",
            category="post",
            strategy_mode="exploit",
            strategy_version=2,
            status="planned",
            claim_run_key=None,
            claimed_at=None,
            finished_at=None,
            detail="",
            created_at=self.now.isoformat(),
        )

    def test_healthy_adaptive_state_builds_safe_shadow_plan_without_writes(self):
        writes = []

        def guarded_execute(query, params=()):
            normalized = query.lstrip().upper()
            if not normalized.startswith("SELECT") and not normalized.startswith("PRAGMA"):
                writes.append(query)
            return self.execute(query, params)

        result = readiness.run_readiness(guarded_execute, now=self.now)
        self.assertEqual(result.status, "ready")
        self.assertEqual(writes, [])
        self.assertEqual(self.get_check(result, "learning").status, "ready")
        self.assertEqual(self.get_check(result, "liveness").status, "ready")
        shadow = self.get_check(result, "shadow_plan")
        self.assertEqual(shadow.status, "ready")
        self.assertIn("2026-09-01", shadow.detail)

    def test_insufficient_learning_uses_safe_baseline_and_is_degraded(self):
        self.conn.execute("UPDATE strategy_stats SET sample_count = 0")
        result = readiness.run_readiness(self.execute, now=self.now)
        self.assertEqual(result.status, "degraded")
        self.assertEqual(self.get_check(result, "learning").status, "degraded")
        self.assertEqual(self.get_check(result, "liveness").status, "ready")
        self.assertEqual(self.get_check(result, "shadow_plan").status, "ready")

    def test_liveness_failure_overrides_learning_degradation(self):
        self.conn.execute("UPDATE strategy_stats SET sample_count = 0")
        self.conn.execute("DELETE FROM daily_plan WHERE plan_date = '2026-08-31'")
        result = readiness.run_readiness(self.execute, now=self.now)
        self.assertEqual(result.status, "failed")
        self.assertEqual(self.get_check(result, "learning").status, "degraded")
        self.assertEqual(self.get_check(result, "liveness").status, "failed")

    def test_duplicate_planner_output_fails_closed(self):
        duplicate = self._slot()
        result = readiness.run_readiness(
            self.execute,
            now=self.now,
            planner_fn=lambda *args, **kwargs: [duplicate, duplicate],
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(self.get_check(result, "shadow_plan").status, "failed")

    def test_unsafe_planner_time_fails_closed(self):
        unsafe = self._slot(
            slot_id="2330-post-01",
            planned_for="2026-09-01T16:30:00+00:00",
        )
        result = readiness.run_readiness(
            self.execute,
            now=self.now,
            planner_fn=lambda *args, **kwargs: [unsafe],
        )
        self.assertEqual(self.get_check(result, "shadow_plan").status, "failed")

    def test_adaptive_slot_without_current_strategy_version_fails_closed(self):
        bad = self._slot()
        bad = DailyPlanSlot(
            plan_date=bad.plan_date,
            slot_id=bad.slot_id,
            planned_for=bad.planned_for,
            action=bad.action,
            category=bad.category,
            strategy_mode=bad.strategy_mode,
            strategy_version=None,
            status=bad.status,
            claim_run_key=bad.claim_run_key,
            claimed_at=bad.claimed_at,
            finished_at=bad.finished_at,
            detail=bad.detail,
            created_at=bad.created_at,
        )
        result = readiness.run_readiness(
            self.execute,
            now=self.now,
            planner_fn=lambda *args, **kwargs: [bad],
        )
        self.assertEqual(self.get_check(result, "shadow_plan").status, "failed")

    def test_planner_exception_is_failed_not_degraded(self):
        def broken_planner(*args, **kwargs):
            raise RuntimeError("planner broken")

        result = readiness.run_readiness(
            self.execute,
            now=self.now,
            planner_fn=broken_planner,
        )
        check = self.get_check(result, "shadow_plan")
        self.assertEqual(check.status, "failed")
        self.assertIn("planner broken", check.detail)


if __name__ == "__main__":
    unittest.main()
