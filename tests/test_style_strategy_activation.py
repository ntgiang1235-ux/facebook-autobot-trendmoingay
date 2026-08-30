import random
import unittest
from unittest.mock import patch

from app.content_models import ContentCandidate
from app.strategy_models import AdaptiveConfig, StrategyStat
from app.style_registry import StyleVariant


class FixedRng:
    def __init__(self, values):
        self.values = iter(values)

    def random(self):
        return next(self.values)


class StyleStrategyActivationTests(unittest.TestCase):
    def _variant(self, idx, dimension, value, status="baseline"):
        return StyleVariant(idx, dimension, value, None, status, "2026-08-30T00:00:00+00:00", None, None)

    def _stat(self, dimension, value, weight, samples=10):
        return StrategyStat(
            dimension=dimension,
            value=value,
            sample_count=samples,
            weighted_score_14d=70.0,
            recent_score_7d=72.0,
            success_rate=0.7,
            current_weight=weight,
            last_used_at=None,
            status="active",
            cooldown_until=None,
            retest_after=None,
            updated_at="2026-08-30T00:00:00+00:00",
        )

    def test_bundle_uses_one_shared_exploit_mode_and_strategy_weights(self):
        from app import style_strategy

        variants = {
            "hook": [self._variant(1, "hook", "question"), self._variant(2, "hook", "number")],
            "tone": [self._variant(3, "tone", "conversational"), self._variant(4, "tone", "witty")],
            "cta": [self._variant(5, "cta", "opinion_question"), self._variant(6, "cta", "no_cta")],
        }
        stats = {
            "hook_type": [self._stat("hook_type", "question", 0.9), self._stat("hook_type", "number", 0.1)],
            "style_type": [self._stat("style_type", "conversational", 0.8), self._stat("style_type", "witty", 0.2)],
            "cta_type": [self._stat("cta_type", "opinion_question", 0.9), self._stat("cta_type", "no_cta", 0.1)],
        }

        with patch.object(style_strategy.style_registry, "ensure_seed_styles"), patch.object(
            style_strategy.style_registry,
            "list_active_styles",
            side_effect=lambda _execute, dimension: variants[dimension],
        ), patch.object(
            style_strategy.strategy_repository,
            "load_config",
            return_value=AdaptiveConfig(exploration_rate=0.20),
        ), patch.object(
            style_strategy.strategy_repository,
            "load_stats",
            side_effect=lambda _execute, dimension=None: stats[dimension],
        ):
            bundle = style_strategy.choose_style_bundle(
                lambda *_: [],
                rng=FixedRng([0.90, 0.10, 0.10, 0.10]),
            )

        self.assertEqual(bundle.mode, "exploit")
        self.assertEqual(bundle.hook_type, "question")
        self.assertEqual(bundle.style_type, "conversational")
        self.assertEqual(bundle.cta_type, "opinion_question")

    def test_bundle_explores_under_sampled_registered_values_with_shared_mode(self):
        from app import style_strategy

        variants = {
            "hook": [self._variant(1, "hook", "question"), self._variant(2, "hook", "number", "explore")],
            "tone": [self._variant(3, "tone", "conversational"), self._variant(4, "tone", "witty", "explore")],
            "cta": [self._variant(5, "cta", "opinion_question"), self._variant(6, "cta", "no_cta", "explore")],
        }
        stats = {
            "hook_type": [self._stat("hook_type", "question", 0.9, 20), self._stat("hook_type", "number", 0.1, 0)],
            "style_type": [self._stat("style_type", "conversational", 0.9, 20), self._stat("style_type", "witty", 0.1, 0)],
            "cta_type": [self._stat("cta_type", "opinion_question", 0.9, 20), self._stat("cta_type", "no_cta", 0.1, 0)],
        }

        with patch.object(style_strategy.style_registry, "ensure_seed_styles"), patch.object(
            style_strategy.style_registry,
            "list_active_styles",
            side_effect=lambda _execute, dimension: variants[dimension],
        ), patch.object(
            style_strategy.strategy_repository,
            "load_config",
            return_value=AdaptiveConfig(exploration_rate=0.20),
        ), patch.object(
            style_strategy.strategy_repository,
            "load_stats",
            side_effect=lambda _execute, dimension=None: stats[dimension],
        ):
            bundle = style_strategy.choose_style_bundle(
                lambda *_: [],
                rng=FixedRng([0.10, 0.95, 0.95, 0.95]),
            )

        self.assertEqual(bundle.mode, "explore")
        self.assertEqual(bundle.hook_type, "number")
        self.assertEqual(bundle.style_type, "witty")
        self.assertEqual(bundle.cta_type, "no_cta")

    def test_style_instruction_is_bounded_and_explicit(self):
        from app.style_strategy import StyleBundle, style_instruction

        text = style_instruction(StyleBundle("question", "witty", "opinion_question", "exploit"))
        self.assertIn("question", text)
        self.assertIn("witty", text)
        self.assertIn("opinion_question", text)
        self.assertLess(len(text), 900)

    def test_publication_ledger_preserves_explicit_candidate_style_metadata(self):
        from app import publication_ledger

        captured = []
        candidate = ContentCandidate(
            category="fun",
            topic_key="fun:topic",
            topic_text="Chủ đề",
            content_text="Nội dung đã gate",
            hook_type="question",
            style_type="witty",
            cta_type="opinion_question",
            format_type="text",
        )

        with patch.object(
            publication_ledger.content_repository,
            "record_candidate",
            side_effect=lambda execute_fn, recorded, **metadata: captured.append((recorded, metadata)) or 1,
        ):
            publication_ledger.record_published_content(
                lambda *_: [],
                action="fun",
                endpoint="me/feed",
                request_data={"message": "Nội dung đã gate"},
                response={"id": "post-1"},
                candidate=candidate,
                quality_score=81.0,
                duplicate_score=0.12,
            )

        recorded, metadata = captured[0]
        self.assertEqual(recorded.hook_type, "question")
        self.assertEqual(recorded.style_type, "witty")
        self.assertEqual(recorded.cta_type, "opinion_question")
        self.assertEqual(metadata["quality_score"], 81.0)
        self.assertEqual(metadata["duplicate_score"], 0.12)


if __name__ == "__main__":
    unittest.main()
