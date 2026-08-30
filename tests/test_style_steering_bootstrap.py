import unittest
from unittest.mock import patch

from app.content_models import ContentCandidate
from app.strategy_models import AdaptiveConfig
from app.style_registry import StyleVariant
from app.style_steering import StyleTarget, restyle_candidate, select_style_target


class SequenceRng:
    def __init__(self, values):
        self.values = list(values)

    def random(self):
        if not self.values:
            return 0.0
        return self.values.pop(0)


def variant(identifier, dimension, value):
    return StyleVariant(
        identifier,
        dimension,
        value,
        None,
        "baseline",
        "2026-08-31T00:00:00+00:00",
        None,
        None,
    )


class StyleBootstrapTests(unittest.TestCase):
    @patch("app.style_steering.load_stats", return_value=[])
    @patch("app.style_steering.load_config")
    def test_without_mature_stats_eighty_percent_path_keeps_legacy_generation(
        self, load_config, _load_stats
    ):
        load_config.return_value = AdaptiveConfig(exploration_rate=0.20)

        target = select_style_target(lambda *_: [], rng=SequenceRng([0.80]))

        self.assertIsNone(target)

    @patch("app.style_steering.list_active_styles")
    @patch("app.style_steering.ensure_seed_styles")
    @patch("app.style_steering.load_stats", return_value=[])
    @patch("app.style_steering.load_config")
    def test_without_mature_stats_twenty_percent_path_runs_controlled_seed_experiment(
        self, load_config, _load_stats, ensure_seed_styles, list_active_styles
    ):
        load_config.return_value = AdaptiveConfig(exploration_rate=0.20)
        by_dimension = {
            "hook": [variant(1, "hook", "question")],
            "tone": [variant(2, "tone", "conversational")],
            "cta": [variant(3, "cta", "opinion_question")],
        }
        list_active_styles.side_effect = lambda _execute, dimension: by_dimension[dimension]

        target = select_style_target(
            lambda *_: [],
            rng=SequenceRng([0.10, 0.0, 0.0, 0.0]),
        )

        ensure_seed_styles.assert_called_once()
        self.assertEqual(
            target,
            StyleTarget(
                hook_type="question",
                style_type="conversational",
                cta_type="opinion_question",
                mode="explore",
            ),
        )

    def test_successful_restyle_labels_exact_style_used_for_future_learning(self):
        candidate = ContentCandidate(
            category="fun",
            topic_key="original",
            topic_text="Bản gốc",
            content_text="Bản gốc",
        )
        target = StyleTarget(
            hook_type="number",
            style_type="witty",
            cta_type="choose_side",
            mode="explore",
        )

        rewritten = restyle_candidate(
            candidate,
            target,
            lambda _prompt: "3 điều vô tri nhưng đúng đến lạ. Bạn chọn phe nào?",
        )

        self.assertEqual(rewritten.hook_type, "number")
        self.assertEqual(rewritten.style_type, "witty")
        self.assertEqual(rewritten.cta_type, "choose_side")
        self.assertNotEqual(rewritten.content_text, candidate.content_text)

    def test_failed_restyle_does_not_fabricate_style_metadata(self):
        candidate = ContentCandidate(
            category="finance",
            topic_key="original",
            topic_text="Bản gốc",
            content_text="Bản gốc",
        )
        target = StyleTarget(
            hook_type="contrast",
            style_type="explanatory",
            cta_type="opinion_question",
            mode="explore",
        )

        rewritten = restyle_candidate(candidate, target, lambda _prompt: None)

        self.assertEqual(rewritten, candidate)
        self.assertEqual(rewritten.hook_type, "unknown")
        self.assertEqual(rewritten.style_type, "unknown")
        self.assertEqual(rewritten.cta_type, "none")


if __name__ == "__main__":
    unittest.main()
