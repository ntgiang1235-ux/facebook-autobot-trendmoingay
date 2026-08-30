import unittest
from datetime import date, datetime, timezone

from app.strategy_models import AdaptiveConfig, StrategyStat


def stat(
    value: str,
    *,
    score: float = 50.0,
    recent: float | None = None,
    samples: int = 8,
    weight: float = 1.0,
    status: str = "active",
    last_used_at: str | None = None,
    retest_after: str | None = None,
) -> StrategyStat:
    return StrategyStat(
        dimension="category",
        value=value,
        sample_count=samples,
        weighted_score_14d=score,
        recent_score_7d=score if recent is None else recent,
        success_rate=0.7 if score >= 50 else 0.2,
        current_weight=weight,
        last_used_at=last_used_at,
        status=status,
        cooldown_until=None,
        retest_after=retest_after,
        updated_at="2026-08-30T00:00:00+00:00",
    )


class DailyPlannerTests(unittest.TestCase):
    def setUp(self):
        self.plan_date = date(2026, 8, 31)
        self.now = datetime(2026, 8, 31, 0, 47, tzinfo=timezone.utc)

    def test_target_daily_volume_is_smooth_and_never_exceeds_twenty_percent_band(self):
        from app.planner import target_daily_volume

        self.assertEqual(target_daily_volume(12, 50.0), 12)
        self.assertEqual(target_daily_volume(12, 100.0), 14)
        self.assertEqual(target_daily_volume(12, 0.0), 10)
        self.assertEqual(target_daily_volume(12, 75.0), 13)
        self.assertEqual(target_daily_volume(12, 25.0), 11)

    def test_kill_switch_uses_deterministic_baseline_with_all_core_categories(self):
        from app.planner import build_daily_plan

        config = AdaptiveConfig(
            adaptive_enabled=False,
            auto_schedule_enabled=False,
            baseline_daily_volume=12,
            current_strategy_version=7,
        )

        first = build_daily_plan(self.plan_date, config, [], [], now=self.now)
        second = build_daily_plan(self.plan_date, config, [], [], now=self.now)

        self.assertEqual(first, second)
        self.assertEqual(len(first), 12)
        self.assertTrue(all(slot.strategy_mode == "baseline" for slot in first))
        self.assertEqual(
            {slot.action for slot in first},
            {"post", "finance", "philosophy", "fun", "recipe", "video"},
        )

    def test_adaptive_plan_excludes_suspended_category_before_retest(self):
        from app.planner import build_daily_plan

        config = AdaptiveConfig(baseline_daily_volume=12, current_strategy_version=9)
        category_stats = [
            stat("post", score=80, weight=0.35),
            stat("video", score=70, weight=0.25),
            stat("fun", score=65, weight=0.15),
            stat("finance", score=60, weight=0.10),
            stat("philosophy", score=55, weight=0.10),
            stat(
                "recipe",
                score=20,
                weight=0.05,
                status="suspended",
                retest_after="2026-09-03T00:00:00+00:00",
            ),
        ]

        slots = build_daily_plan(self.plan_date, config, category_stats, [], now=self.now)

        self.assertNotIn("recipe", [slot.action for slot in slots])
        self.assertLessEqual(len(slots), 14)
        self.assertGreaterEqual(len(slots), 10)
        self.assertTrue(all(slot.strategy_version == 9 for slot in slots))

    def test_due_suspended_category_gets_exactly_one_controlled_retest_slot(self):
        from app.planner import build_daily_plan

        config = AdaptiveConfig(baseline_daily_volume=12, current_strategy_version=10)
        category_stats = [
            stat("post", score=75, weight=0.35),
            stat("video", score=70, weight=0.25),
            stat("fun", score=60, weight=0.15),
            stat("finance", score=58, weight=0.10),
            stat("philosophy", score=55, weight=0.10),
            stat(
                "recipe",
                score=20,
                weight=0.05,
                status="suspended",
                retest_after="2026-08-30T00:00:00+00:00",
            ),
        ]

        slots = build_daily_plan(self.plan_date, config, category_stats, [], now=self.now)
        recipe_slots = [slot for slot in slots if slot.action == "recipe"]

        self.assertEqual(len(recipe_slots), 1)
        self.assertEqual(recipe_slots[0].strategy_mode, "retest")

    def test_plan_uses_unique_chronological_half_hour_buckets_stored_as_utc(self):
        from app.planner import build_daily_plan

        config = AdaptiveConfig(
            adaptive_enabled=False,
            auto_schedule_enabled=False,
            baseline_daily_volume=12,
        )
        slots = build_daily_plan(self.plan_date, config, [], [], now=self.now)

        planned = [datetime.fromisoformat(slot.planned_for) for slot in slots]
        self.assertEqual(planned, sorted(planned))
        self.assertEqual(len(planned), len(set(planned)))
        self.assertTrue(all(value.tzinfo == timezone.utc for value in planned))
        # 08:30 Vietnam = 01:30 UTC.
        self.assertEqual(planned[0].hour, 1)
        self.assertEqual(planned[0].minute, 30)
        self.assertTrue(all(value.minute in {0, 30} for value in planned))

    def test_insufficient_learning_data_falls_back_to_baseline_instead_of_overfitting(self):
        from app.planner import build_daily_plan

        config = AdaptiveConfig(baseline_daily_volume=12, current_strategy_version=11)
        category_stats = [
            stat("post", score=99, samples=2, weight=0.9),
            stat("video", score=1, samples=2, weight=0.1),
        ]

        slots = build_daily_plan(self.plan_date, config, category_stats, [], now=self.now)

        self.assertEqual(len(slots), 12)
        self.assertTrue(all(slot.strategy_mode == "baseline" for slot in slots))


if __name__ == "__main__":
    unittest.main()
