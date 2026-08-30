import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from app.strategy_models import AdaptiveConfig, StrategyStat


class AdaptivePlannerJobTests(unittest.TestCase):
    def test_create_daily_plan_loads_strategy_builds_and_saves_idempotent_slots(self):
        from app import adaptive_jobs

        execute = Mock()
        config = AdaptiveConfig(current_strategy_version=12)
        category = StrategyStat(
            dimension="category",
            value="post",
            sample_count=8,
            weighted_score_14d=70.0,
            recent_score_7d=75.0,
            success_rate=0.7,
            current_weight=0.4,
            last_used_at=None,
            status="active",
            cooldown_until=None,
            retest_after=None,
            updated_at="now",
        )
        slot = Mock()
        now = datetime(2026, 8, 31, 0, 47, tzinfo=timezone.utc)

        with patch.object(adaptive_jobs.strategy_repository, "load_config", return_value=config) as load_config, patch.object(
            adaptive_jobs.strategy_repository,
            "load_stats",
            side_effect=[[category], []],
        ) as load_stats, patch.object(
            adaptive_jobs.planner, "build_daily_plan", return_value=[slot]
        ) as build, patch.object(
            adaptive_jobs.plan_repository, "save_slots"
        ) as save:
            outcome = adaptive_jobs.create_daily_plan(execute, now=now)

        self.assertEqual(outcome.status, "success")
        self.assertIn("1", outcome.detail)
        load_config.assert_called_once_with(execute)
        self.assertEqual(load_stats.call_args_list[0].args, (execute, "category"))
        self.assertEqual(load_stats.call_args_list[1].args, (execute, "time_bucket"))
        self.assertEqual(build.call_args.args[0].isoformat(), "2026-08-31")
        self.assertEqual(build.call_args.kwargs["now"], now)
        save.assert_called_once_with(execute, [slot])

    def test_create_daily_plan_uses_vietnam_local_date(self):
        from app import adaptive_jobs

        execute = Mock()
        late_utc = datetime(2026, 8, 30, 18, 10, tzinfo=timezone.utc)
        with patch.object(
            adaptive_jobs.strategy_repository, "load_config", return_value=AdaptiveConfig()
        ), patch.object(adaptive_jobs.strategy_repository, "load_stats", return_value=[]), patch.object(
            adaptive_jobs.planner, "build_daily_plan", return_value=[]
        ) as build, patch.object(adaptive_jobs.plan_repository, "save_slots"):
            adaptive_jobs.create_daily_plan(execute, now=late_utc)

        self.assertEqual(build.call_args.args[0].isoformat(), "2026-08-31")


if __name__ == "__main__":
    unittest.main()
