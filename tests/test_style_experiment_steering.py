import unittest
from unittest.mock import patch

from app.strategy_models import AdaptiveConfig, StrategyStat
from app.style_registry import StyleVariant


class FakeRng:
    def __init__(self, values):
        self.values = iter(values)

    def random(self):
        return next(self.values)


def stat(dimension, value, weight=1.0):
    return StrategyStat(
        dimension=dimension,
        value=value,
        sample_count=8,
        weighted_score_14d=80.0,
        recent_score_7d=80.0,
        success_rate=0.7,
        current_weight=weight,
        last_used_at=None,
        status="active",
        cooldown_until=None,
        retest_after=None,
        updated_at="2026-08-31T00:00:00+00:00",
    )


def variant(identifier, dimension, value, status, parent=None):
    return StyleVariant(
        id=identifier,
        dimension=dimension,
        value=value,
        parent_value=parent,
        status=status,
        created_at="2026-08-31T00:00:00+00:00",
        promoted_at=None,
        retired_at=None,
    )


class StyleExperimentSteeringTests(unittest.TestCase):
    def _stats(self):
        return [
            stat("hook_type", "number"),
            stat("style_type", "witty"),
            stat("cta_type", "opinion_question"),
        ]

    @patch("app.style_steering.list_active_styles")
    @patch("app.style_steering.ensure_seed_styles")
    @patch("app.style_steering.load_stats")
    @patch("app.style_steering.load_config")
    def test_explore_mode_exposes_exactly_one_registered_experiment(
        self, load_config, load_stats, _seed, list_styles
    ):
        from app.style_steering import select_style_target

        load_config.return_value = AdaptiveConfig(exploration_rate=0.20)
        load_stats.return_value = self._stats()
        registry = {
            "hook": [variant(1, "hook", "number", "baseline")],
            "tone": [
                variant(2, "tone", "witty", "baseline"),
                variant(9, "tone", "witty_short_punchline", "explore", "witty"),
            ],
            "cta": [variant(3, "cta", "opinion_question", "baseline")],
        }
        list_styles.side_effect = lambda _execute, dimension: registry[dimension]

        target = select_style_target(
            lambda *_: [],
            FakeRng([0.10, 0.0, 0.0, 0.0]),
        )

        self.assertEqual(target.mode, "explore")
        self.assertEqual(target.experiment_key, "tone:witty_short_punchline")
        self.assertEqual(target.hook_type, "number")
        self.assertEqual(target.style_type, "witty_short_punchline")
        self.assertEqual(target.cta_type, "opinion_question")

    @patch("app.style_steering.list_active_styles")
    @patch("app.style_steering.ensure_seed_styles")
    @patch("app.style_steering.load_stats")
    @patch("app.style_steering.load_config")
    def test_exploit_mode_never_uses_pending_experiment(
        self, load_config, load_stats, _seed, list_styles
    ):
        from app.style_steering import select_style_target

        load_config.return_value = AdaptiveConfig(exploration_rate=0.20)
        load_stats.return_value = self._stats()
        registry = {
            "hook": [],
            "tone": [variant(9, "tone", "witty_short_punchline", "explore", "witty")],
            "cta": [],
        }
        list_styles.side_effect = lambda _execute, dimension: registry[dimension]

        target = select_style_target(
            lambda *_: [],
            FakeRng([0.90, 0.0, 0.0, 0.0]),
        )

        self.assertEqual(target.mode, "exploit")
        self.assertIsNone(target.experiment_key)
        self.assertEqual(target.style_type, "witty")

    @patch("app.style_steering.list_active_styles")
    @patch("app.style_steering.ensure_seed_styles")
    @patch("app.style_steering.load_stats")
    @patch("app.style_steering.load_config")
    def test_exploit_of_promoted_custom_style_preserves_treatment_key(
        self, load_config, load_stats, _seed, list_styles
    ):
        from app.style_steering import select_style_target

        load_config.return_value = AdaptiveConfig(exploration_rate=0.20)
        load_stats.return_value = [
            stat("hook_type", "number"),
            stat("style_type", "witty_short_punchline"),
            stat("cta_type", "opinion_question"),
        ]
        registry = {
            "hook": [variant(1, "hook", "number", "baseline")],
            "tone": [
                variant(2, "tone", "witty", "baseline"),
                variant(9, "tone", "witty_short_punchline", "active", "witty"),
            ],
            "cta": [variant(3, "cta", "opinion_question", "baseline")],
        }
        list_styles.side_effect = lambda _execute, dimension: registry[dimension]

        target = select_style_target(
            lambda *_: [],
            FakeRng([0.90, 0.0, 0.0, 0.0]),
        )

        self.assertEqual(target.mode, "exploit")
        self.assertEqual(target.style_type, "witty_short_punchline")
        self.assertEqual(target.experiment_key, "tone:witty_short_punchline")

    @patch("app.style_steering.list_active_styles")
    @patch("app.style_steering.ensure_seed_styles")
    @patch("app.style_steering.load_stats")
    @patch("app.style_steering.load_config")
    def test_multiple_pending_experiments_still_exposes_only_one_dimension(
        self, load_config, load_stats, _seed, list_styles
    ):
        from app.style_steering import select_style_target

        load_config.return_value = AdaptiveConfig(exploration_rate=0.20)
        load_stats.return_value = self._stats()
        registry = {
            "hook": [variant(7, "hook", "number_with_tension", "explore", "number")],
            "tone": [variant(9, "tone", "witty_short_punchline", "explore", "witty")],
            "cta": [],
        }
        list_styles.side_effect = lambda _execute, dimension: registry[dimension]

        target = select_style_target(
            lambda *_: [],
            FakeRng([0.10, 0.0, 0.0, 0.0]),
        )

        self.assertEqual(target.experiment_key, "hook:number_with_tension")
        self.assertEqual(target.hook_type, "number_with_tension")
        self.assertEqual(target.style_type, "witty")
        self.assertEqual(target.cta_type, "opinion_question")


if __name__ == "__main__":
    unittest.main()
