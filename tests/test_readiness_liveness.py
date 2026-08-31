import sqlite3
import unittest
from datetime import datetime, timezone

from app import readiness
from app.strategy_models import AdaptiveConfig


class ReadinessLivenessTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(
            """
            CREATE TABLE daily_plan (
                plan_date TEXT, slot_id TEXT, planned_for TEXT, action TEXT,
                category TEXT, strategy_mode TEXT, strategy_version INTEGER,
                status TEXT, claim_run_key TEXT, claimed_at TEXT,
                finished_at TEXT, detail TEXT, created_at TEXT
            );
            CREATE TABLE job_runs (
                run_key TEXT PRIMARY KEY, action TEXT, status TEXT,
                started_at TEXT, finished_at TEXT, detail TEXT,
                scheduled_for TEXT, delay_minutes INTEGER
            );
            CREATE TABLE content_posts (
                facebook_post_id TEXT, published_at TEXT, status TEXT,
                strategy_mode TEXT, scheduled_for TEXT
            );
            """
        )
        self.config = AdaptiveConfig(
            adaptive_enabled=True,
            auto_schedule_enabled=True,
            auto_suspend_enabled=True,
            exploration_rate=0.2,
            baseline_daily_volume=12,
            current_strategy_version=1,
            last_good_strategy_version=None,
        )

    def tearDown(self):
        self.conn.close()

    def execute(self, query, params=()):
        return self.conn.execute(query, params).fetchall()

    def insert_plan(self, *planned_times):
        for index, planned_for in enumerate(planned_times, start=1):
            self.conn.execute(
                """
                INSERT INTO daily_plan VALUES (
                    '2026-08-31', ?, ?, 'post', 'post', 'baseline', 1,
                    'planned', NULL, NULL, NULL, '', '2026-08-31T00:47:00+00:00'
                )
                """,
                (f"slot-{index}", planned_for),
            )
        self.conn.commit()

    def insert_dispatch(self, started_at, status="skipped"):
        self.conn.execute(
            "INSERT INTO job_runs VALUES ('dispatch-run', 'dispatch', ?, ?, ?, '', NULL, NULL)",
            (status, started_at, started_at),
        )
        self.conn.commit()

    def insert_published(self, published_at, *, strategy_mode="baseline", scheduled_for=None):
        scheduled = scheduled_for or published_at
        self.conn.execute(
            "INSERT INTO content_posts VALUES ('fb-post-1', ?, 'published', ?, ?)",
            (published_at, strategy_mode, scheduled),
        )
        self.conn.commit()

    def test_missing_plan_before_first_safe_slot_is_degraded(self):
        now = datetime(2026, 8, 31, 1, 0, tzinfo=timezone.utc)  # 08:00 VN
        check = readiness._liveness_check(self.execute, self.config, now)
        self.assertEqual(check.status, "degraded")
        self.assertIn("before 08:30", check.detail)

    def test_missing_plan_after_first_safe_slot_is_failed(self):
        now = datetime(2026, 8, 31, 2, 0, tzinfo=timezone.utc)  # 09:00 VN
        check = readiness._liveness_check(self.execute, self.config, now)
        self.assertEqual(check.status, "failed")
        self.assertIn("no persisted daily_plan", check.detail)

    def test_dispatcher_older_than_ninety_minutes_is_failed(self):
        self.insert_plan("2026-08-31T01:30:00+00:00")
        self.insert_dispatch("2026-08-31T01:00:00+00:00")
        now = datetime(2026, 8, 31, 3, 0, tzinfo=timezone.utc)  # 10:00 VN

        check = readiness._liveness_check(self.execute, self.config, now)
        self.assertEqual(check.status, "failed")
        self.assertIn("dispatcher", check.detail)
        self.assertIn("90", check.detail)

    def test_three_elapsed_slots_with_zero_publications_is_failed(self):
        self.insert_plan(
            "2026-08-31T01:30:00+00:00",
            "2026-08-31T02:00:00+00:00",
            "2026-08-31T02:30:00+00:00",
            "2026-08-31T03:00:00+00:00",
        )
        self.insert_dispatch("2026-08-31T03:20:00+00:00")
        now = datetime(2026, 8, 31, 3, 30, tzinfo=timezone.utc)  # 10:30 VN

        check = readiness._liveness_check(self.execute, self.config, now)
        self.assertEqual(check.status, "failed")
        self.assertIn("0 published", check.detail)
        self.assertIn("elapsed", check.detail)

    def test_manual_publish_does_not_mask_elapsed_scheduler_outage(self):
        self.insert_plan(
            "2026-08-31T01:30:00+00:00",
            "2026-08-31T02:00:00+00:00",
            "2026-08-31T02:30:00+00:00",
            "2026-08-31T03:00:00+00:00",
        )
        self.insert_dispatch("2026-08-31T03:20:00+00:00")
        self.insert_published(
            "2026-08-31T02:10:00+00:00",
            strategy_mode="manual",
            scheduled_for=None,
        )
        now = datetime(2026, 8, 31, 3, 30, tzinfo=timezone.utc)

        check = readiness._liveness_check(self.execute, self.config, now)
        self.assertEqual(check.status, "failed")
        self.assertIn("0 published", check.detail)

    def test_recent_dispatch_and_publication_is_ready(self):
        self.insert_plan(
            "2026-08-31T01:30:00+00:00",
            "2026-08-31T02:00:00+00:00",
            "2026-08-31T02:30:00+00:00",
            "2026-08-31T03:00:00+00:00",
        )
        self.insert_dispatch("2026-08-31T03:20:00+00:00")
        self.insert_published("2026-08-31T02:10:00+00:00")
        now = datetime(2026, 8, 31, 3, 30, tzinfo=timezone.utc)

        check = readiness._liveness_check(self.execute, self.config, now)
        self.assertEqual(check.status, "ready")
        self.assertIn("published=1", check.detail)
        self.assertIn("plan=4", check.detail)

    def test_disabled_auto_schedule_is_ready_without_operational_state(self):
        config = AdaptiveConfig(
            adaptive_enabled=True,
            auto_schedule_enabled=False,
            auto_suspend_enabled=True,
            exploration_rate=0.2,
            baseline_daily_volume=12,
        )
        now = datetime(2026, 8, 31, 3, 30, tzinfo=timezone.utc)
        check = readiness._liveness_check(self.execute, config, now)
        self.assertEqual(check.status, "ready")


if __name__ == "__main__":
    unittest.main()
