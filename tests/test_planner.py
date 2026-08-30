import unittest
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

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
    dimension: str = "category",
) -> StrategyStat:
    return StrategyStat(
        dimension=dimension,
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


def ready_categories() -> list[StrategyStat]:
    return [
        stat("post", weight=0.30),
        stat("video", weight=0.25),
        stat("fun", weight=0.15),
        stat("finance", weight=0.10),
        stat("philosophy", weight=0.10),
        stat("recipe", weight=0.10),
    ]


def local_times(slots) -> list[str]:
    vietnam = ZoneInfo("Asia/Ho_Chi_Minh")
    return [
        datetime.fromisoformat(slot.planned_for).astimezone(vietnam).strftime("%H:%M")
        for slot in slots
    ]


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

    def test_insufficient_time_learning_keeps_baseline_time_buckets(self):
        from app.planner import build_daily_plan

        config = AdaptiveConfig(baseline_daily_volume=12, current_strategy_version=12)
        time_stats = [
            stat("15:30", dimension="time_bucket", samples=2, weight=0.90),
            stat("19:30", dimension="time_bucket", samples=2, weight=0.10),
        ]

        slots = build_daily_plan(
            self.plan_date,
            config,
            ready_categories(),
            time_stats,
            now=self.now,
        )

        self.assertEqual(
            local_times(slots),
            [hhmm for hhmm, _ in (
                ("08:30", "post"), ("09:00", "philosophy"), ("09:30", "video"),
                ("11:00", "finance"), ("11:30", "post"), ("12:30", "video"),
                ("13:30", "fun"), ("14:30", "post"), ("16:00", "recipe"),
                ("17:30", "video"), ("18:30", "post"), ("20:00", "fun"),
            )],
        )

    def test_mature_time_learning_can_move_plan_into_winning_nonbaseline_bucket(self):
        from app.planner import build_daily_plan

        config = AdaptiveConfig(baseline_daily_volume=12, current_strategy_version=13)
        learned = [
            ("08:30", 0.10), ("09:00", 0.10), ("09:30", 0.10),
            ("10:00", 0.10), ("10:30", 0.10), ("11:00", 0.10),
            ("11:30", 0.10), ("12:00", 0.10), ("12:30", 0.10),
            ("13:00", 0.10), ("13:30", 0.10), ("15:30", 5.00),
            ("18:30", 0.10), ("20:00", 0.10),
        ]
        time_stats = [
            stat(value, dimension="time_bucket", samples=8, score=80 if value == "15:30" else 50, weight=weight)
            for value, weight in learned
        ]

        slots = build_daily_plan(
            self.plan_date,
            config,
            ready_categories(),
            time_stats,
            now=self.now,
        )

        selected = local_times(slots)
        self.assertIn("15:30", selected)
        self.assertEqual(len(selected), len(set(selected)))
        self.assertTrue(all("08:30" <= value <= "21:00" for value in selected))

    def test_suspended_time_bucket_is_excluded_from_adaptive_schedule(self):
        from app.planner import build_daily_plan

        config = AdaptiveConfig(baseline_daily_volume=12, current_strategy_version=14)
        values = [
            "08:30", "09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
            "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00",
            "15:30", "16:00",
        ]
        time_stats = [
            stat(
                value,
                dimension="time_bucket",
                samples=8,
                weight=10.0 if value == "13:30" else 1.0,
                status="suspended" if value == "13:30" else "active",
            )
            for value in values
        ]

        slots = build_daily_plan(
            self.plan_date,
            config,
            ready_categories(),
            time_stats,
            now=self.now,
        )

        self.assertNotIn("13:30", local_times(slots))


if __name__ == "__main__":
    unittest.main()
