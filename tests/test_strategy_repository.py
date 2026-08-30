import unittest


class StrategyRepositoryTests(unittest.TestCase):
    def test_load_config_returns_deterministic_defaults_when_missing(self):
        from app.strategy_repository import load_config

        config = load_config(lambda query, params=(): [])

        self.assertTrue(config.adaptive_enabled)
        self.assertTrue(config.auto_schedule_enabled)
        self.assertTrue(config.auto_suspend_enabled)
        self.assertEqual(config.exploration_rate, 0.20)
        self.assertGreater(config.baseline_daily_volume, 0)
        self.assertIsNone(config.current_strategy_version)
        self.assertIsNone(config.last_good_strategy_version)

    def test_save_config_upserts_single_typed_row(self):
        from app.strategy_models import AdaptiveConfig
        from app.strategy_repository import save_config

        calls = []
        config = AdaptiveConfig(
            adaptive_enabled=False,
            auto_schedule_enabled=True,
            auto_suspend_enabled=False,
            exploration_rate=0.15,
            baseline_daily_volume=10,
            current_strategy_version=7,
            last_good_strategy_version=6,
        )

        save_config(lambda query, params=(): calls.append((query, params)) or [], config)

        self.assertEqual(len(calls), 1)
        query, params = calls[0]
        self.assertIn("adaptive_config", query)
        self.assertIn("ON CONFLICT", query)
        self.assertEqual(
            params,
            (1, 0, 1, 0, 0.15, 10, 7, 6),
        )

    def test_load_stats_maps_typed_rows_and_can_filter_dimension(self):
        from app.strategy_repository import load_stats

        calls = []

        def execute(query, params=()):
            calls.append((query, params))
            return [
                (
                    "hook",
                    "numbered",
                    8,
                    74.5,
                    79.0,
                    0.625,
                    0.35,
                    "2026-08-30T01:00:00+00:00",
                    "active",
                    None,
                    None,
                    "2026-08-30T02:00:00+00:00",
                )
            ]

        stats = load_stats(execute, dimension="hook")

        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0].dimension, "hook")
        self.assertEqual(stats[0].value, "numbered")
        self.assertEqual(stats[0].sample_count, 8)
        self.assertEqual(stats[0].current_weight, 0.35)
        self.assertEqual(stats[0].status, "active")
        self.assertIn("WHERE dimension = ?", calls[0][0])
        self.assertEqual(calls[0][1], ("hook",))

    def test_upsert_stat_uses_dimension_value_identity(self):
        from app.strategy_models import StrategyStat
        from app.strategy_repository import upsert_stat

        calls = []
        stat = StrategyStat(
            dimension="category",
            value="finance",
            sample_count=6,
            weighted_score_14d=71.0,
            recent_score_7d=76.0,
            success_rate=0.67,
            current_weight=0.25,
            last_used_at="2026-08-30T01:00:00+00:00",
            status="active",
            cooldown_until=None,
            retest_after=None,
            updated_at="2026-08-30T02:00:00+00:00",
        )

        upsert_stat(lambda query, params=(): calls.append((query, params)) or [], stat)

        query, params = calls[0]
        self.assertIn("ON CONFLICT(dimension, value) DO UPDATE", query)
        self.assertEqual(params[0:2], ("category", "finance"))
        self.assertEqual(params[-1], "2026-08-30T02:00:00+00:00")

    def test_save_strategy_version_is_insert_only_and_serializes_snapshot(self):
        from app.strategy_models import AdaptiveConfig, StrategySnapshot
        from app.strategy_repository import save_strategy_version

        calls = []
        snapshot = StrategySnapshot(
            version_id=12,
            weights={"category": {"finance": 0.6, "fun": 0.4}},
            config=AdaptiveConfig(
                adaptive_enabled=True,
                auto_schedule_enabled=True,
                auto_suspend_enabled=True,
                exploration_rate=0.20,
                baseline_daily_volume=10,
                current_strategy_version=12,
                last_good_strategy_version=11,
            ),
            created_at="2026-08-30T02:00:00+00:00",
            reason="daily learning",
            is_last_good=False,
        )

        save_strategy_version(lambda query, params=(): calls.append((query, params)) or [], snapshot)

        self.assertEqual(len(calls), 1)
        query, params = calls[0]
        self.assertIn("INSERT INTO strategy_versions", query)
        self.assertNotIn("ON CONFLICT", query.upper())
        self.assertNotIn("UPDATE strategy_versions", query)
        self.assertEqual(params[0], 12)
        self.assertIn('"finance": 0.6', params[1])
        self.assertIn('"exploration_rate": 0.2', params[2])
        self.assertEqual(params[3:], ("2026-08-30T02:00:00+00:00", "daily learning", 0))

    def test_load_strategy_version_maps_serialized_snapshot(self):
        from app.strategy_repository import load_strategy_version

        def execute(query, params=()):
            self.assertIn("FROM strategy_versions", query)
            self.assertEqual(params, (12,))
            return [
                (
                    12,
                    '{"category":{"finance":0.6,"fun":0.4}}',
                    '{"adaptive_enabled":true,"auto_schedule_enabled":true,"auto_suspend_enabled":true,"exploration_rate":0.2,"baseline_daily_volume":10,"current_strategy_version":12,"last_good_strategy_version":11}',
                    "2026-08-30T02:00:00+00:00",
                    "daily learning",
                    1,
                )
            ]

        snapshot = load_strategy_version(execute, 12)

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.version_id, 12)
        self.assertEqual(snapshot.weights["category"]["finance"], 0.6)
        self.assertEqual(snapshot.config.exploration_rate, 0.20)
        self.assertTrue(snapshot.is_last_good)


if __name__ == "__main__":
    unittest.main()
