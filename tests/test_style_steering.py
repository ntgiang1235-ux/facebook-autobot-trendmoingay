import unittest
from unittest.mock import patch

from app.content_models import ContentCandidate
from app.strategy_models import AdaptiveConfig, StrategyStat
from app.style_registry import StyleVariant


class FakeRng:
    def __init__(self, values):
        self.values = iter(values)

    def random(self):
        return next(self.values)


def stat(dimension, value, *, weight=1.0, samples=5, status="active"):
    return StrategyStat(
        dimension=dimension,
        value=value,
        sample_count=samples,
        weighted_score_14d=75.0,
        recent_score_7d=75.0,
        success_rate=0.7,
        current_weight=weight,
        last_used_at=None,
        status=status,
        cooldown_until=None,
        retest_after=None,
        updated_at="2026-08-30T00:00:00+00:00",
    )


def variant(identifier, dimension, value, status="baseline"):
    return StyleVariant(
        id=identifier,
        dimension=dimension,
        value=value,
        parent_value=None,
        status=status,
        created_at="2026-08-30T00:00:00+00:00",
        promoted_at=None,
        retired_at=None,
    )


class StyleTargetSelectionTests(unittest.TestCase):
    def test_insufficient_mature_style_data_returns_no_target(self):
        from app.style_steering import select_style_target

        stats = [
            stat("hook_type", "number", samples=4, status="insufficient_data"),
            stat("style_type", "explanatory", samples=4, status="insufficient_data"),
            stat("cta_type", "opinion_question", samples=4, status="insufficient_data"),
        ]
        with patch("app.style_steering.load_config", return_value=AdaptiveConfig()), patch(
            "app.style_steering.load_stats", return_value=stats
        ), patch("app.style_steering.ensure_seed_styles"):
            target = select_style_target(lambda *args: [], FakeRng([0.9]))

        self.assertIsNone(target)

    def test_exploit_uses_only_mature_active_strategy_values(self):
        from app.style_steering import select_style_target

        stats = [
            stat("hook_type", "number", weight=0.9),
            stat("hook_type", "question", weight=0.1, samples=4, status="insufficient_data"),
            stat("style_type", "explanatory", weight=1.0),
            stat("cta_type", "opinion_question", weight=1.0),
        ]
        with patch("app.style_steering.load_config", return_value=AdaptiveConfig(exploration_rate=0.2)), patch(
            "app.style_steering.load_stats", return_value=stats
        ), patch("app.style_steering.ensure_seed_styles"):
            target = select_style_target(
                lambda *args: [],
                FakeRng([0.9, 0.2, 0.2, 0.2]),
            )

        self.assertEqual(target.mode, "exploit")
        self.assertEqual(target.hook_type, "number")
        self.assertEqual(target.style_type, "explanatory")
        self.assertEqual(target.cta_type, "opinion_question")

    def test_explore_can_choose_registered_unseen_variants(self):
        from app.style_steering import select_style_target

        stats = [
            stat("hook_type", "number", samples=8),
            stat("style_type", "explanatory", samples=8),
            stat("cta_type", "opinion_question", samples=8),
        ]
        registered = {
            "hook": [variant(1, "hook", "curiosity")],
            "tone": [variant(2, "tone", "witty")],
            "cta": [variant(3, "cta", "experience_share")],
        }

        def active_styles(_execute, dimension):
            return registered[dimension]

        with patch("app.style_steering.load_config", return_value=AdaptiveConfig(exploration_rate=0.2)), patch(
            "app.style_steering.load_stats", return_value=stats
        ), patch("app.style_steering.ensure_seed_styles"), patch(
            "app.style_steering.list_active_styles", side_effect=active_styles
        ):
            target = select_style_target(
                lambda *args: [],
                FakeRng([0.1, 0.2, 0.2, 0.2]),
            )

        self.assertEqual(target.mode, "explore")
        self.assertEqual(target.hook_type, "curiosity")
        self.assertEqual(target.style_type, "witty")
        self.assertEqual(target.cta_type, "experience_share")

    def test_adaptive_kill_switch_disables_style_steering(self):
        from app.style_steering import select_style_target

        with patch(
            "app.style_steering.load_config",
            return_value=AdaptiveConfig(adaptive_enabled=False),
        ), patch("app.style_steering.ensure_seed_styles") as seed:
            target = select_style_target(lambda *args: [], FakeRng([0.1]))

        self.assertIsNone(target)
        seed.assert_not_called()


class StyleRestyleTests(unittest.TestCase):
    def test_restyle_preserves_source_and_leaves_actual_labels_for_quality_classifier(self):
        from app.style_steering import StyleTarget, restyle_candidate

        candidate = ContentCandidate(
            category="finance",
            topic_key="gold-price",
            topic_text="Giá vàng hôm nay",
            content_text="Giá vàng tăng 2%. Nguồn: Example.",
            source_url="https://example.com/gold",
            format_type="text",
        )
        prompts = []

        def gemini(prompt):
            prompts.append(prompt)
            return "3 điểm đáng chú ý: Giá vàng tăng 2%. Nguồn: Example. Bạn nghĩ sao?"

        result = restyle_candidate(
            candidate,
            StyleTarget("number", "explanatory", "opinion_question", "exploit"),
            gemini,
        )

        self.assertEqual(len(prompts), 1)
        self.assertIn("number", prompts[0])
        self.assertIn("explanatory", prompts[0])
        self.assertIn("opinion_question", prompts[0])
        self.assertIn("Do not invent facts", prompts[0])
        self.assertEqual(result.source_url, candidate.source_url)
        self.assertEqual(result.content_text, "3 điểm đáng chú ý: Giá vàng tăng 2%. Nguồn: Example. Bạn nghĩ sao?")
        self.assertEqual(result.hook_type, "unknown")
        self.assertEqual(result.style_type, "unknown")
        self.assertEqual(result.cta_type, "none")

    def test_restyle_failure_falls_back_to_original_candidate(self):
        from app.style_steering import StyleTarget, restyle_candidate

        candidate = ContentCandidate(
            category="fun",
            topic_key="sleep",
            topic_text="Thức khuya",
            content_text="Thức khuya lướt điện thoại.",
        )
        target = StyleTarget("question", "witty", "experience_share", "explore")

        self.assertIs(restyle_candidate(candidate, target, lambda prompt: None), candidate)


if __name__ == "__main__":
    unittest.main()
