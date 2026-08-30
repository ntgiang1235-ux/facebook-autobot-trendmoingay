import unittest

from app.strategy_models import StrategyStat


class FakeRng:
    def __init__(self, *values):
        self.values = list(values)
        self.index = 0

    def random(self):
        if self.index >= len(self.values):
            raise AssertionError("FakeRng exhausted")
        value = self.values[self.index]
        self.index += 1
        return value


class SelectionTests(unittest.TestCase):
    def stat(self, value, weight, score=60.0, samples=6, status="active"):
        return StrategyStat(
            dimension="hook",
            value=value,
            sample_count=samples,
            weighted_score_14d=score,
            recent_score_7d=score,
            success_rate=0.60,
            current_weight=weight,
            last_used_at=None,
            status=status,
            cooldown_until=None,
            retest_after=None,
            updated_at="2026-08-30T07:00:00+00:00",
        )

    def test_select_mode_uses_strict_twenty_percent_boundary(self):
        from app.selection import select_mode

        self.assertEqual(select_mode(FakeRng(0.1999), 0.20), "explore")
        self.assertEqual(select_mode(FakeRng(0.20), 0.20), "exploit")

    def test_weighted_choice_is_deterministic_with_injected_rng(self):
        from app.selection import weighted_choice

        options = [("a", 0.75), ("b", 0.25)]
        self.assertEqual(weighted_choice(options, FakeRng(0.10)), "a")
        self.assertEqual(weighted_choice(options, FakeRng(0.90)), "b")

    def test_exploit_excludes_suspended_values(self):
        from app.selection import select_strategy

        stats = [
            self.stat("winner", 0.80, score=85.0),
            self.stat("sleeping", 0.20, score=95.0, status="suspended"),
        ]
        selection = select_strategy(stats, [], FakeRng(0.80, 0.95), 0.20)

        self.assertEqual(selection.mode, "exploit")
        self.assertEqual(selection.value, "winner")
        self.assertNotIn("suspended", selection.reason.lower())

    def test_exploit_uses_weights_so_high_performer_has_more_probability_but_not_one(self):
        from app.selection import exploit_probabilities

        stats = [self.stat("high", 0.80), self.stat("low", 0.20)]
        probabilities = exploit_probabilities(stats)

        self.assertGreater(probabilities["high"], probabilities["low"])
        self.assertLess(probabilities["high"], 1.0)
        self.assertGreater(probabilities["low"], 0.0)
        self.assertAlmostEqual(sum(probabilities.values()), 1.0)

    def test_explore_prefers_unseen_variant_over_heavily_sampled_variant(self):
        from app.selection import exploration_probabilities

        stats = [self.stat("known", 0.50, samples=10)]
        probabilities = exploration_probabilities(stats, ["known", "new_variant"])

        self.assertGreater(probabilities["new_variant"], probabilities["known"])
        self.assertAlmostEqual(sum(probabilities.values()), 1.0)

    def test_explore_prefers_lower_sample_count_among_registered_variants(self):
        from app.selection import exploration_probabilities

        stats = [
            self.stat("fresh", 0.30, samples=1),
            self.stat("mature", 0.70, samples=10),
        ]
        probabilities = exploration_probabilities(stats, ["fresh", "mature"])

        self.assertGreater(probabilities["fresh"], probabilities["mature"])

    def test_empty_exploration_pool_falls_back_to_exploit(self):
        from app.selection import select_strategy

        stats = [self.stat("a", 0.60), self.stat("b", 0.40)]
        selection = select_strategy(stats, [], FakeRng(0.10, 0.20), 0.20)

        self.assertEqual(selection.mode, "exploit")
        self.assertIn("fallback", selection.reason.lower())
        self.assertIn(selection.value, {"a", "b"})

    def test_explore_does_not_reintroduce_suspended_registered_value(self):
        from app.selection import exploration_probabilities

        stats = [
            self.stat("active", 0.70, samples=5),
            self.stat("sleeping", 0.0, samples=8, status="suspended"),
        ]
        probabilities = exploration_probabilities(stats, ["active", "sleeping", "new_variant"])

        self.assertNotIn("sleeping", probabilities)
        self.assertIn("new_variant", probabilities)

    def test_selection_is_reproducible_for_same_rng_sequence(self):
        from app.selection import select_strategy

        stats = [self.stat("a", 0.60), self.stat("b", 0.40)]
        first = select_strategy(stats, ["new"], FakeRng(0.50, 0.70), 0.20)
        second = select_strategy(stats, ["new"], FakeRng(0.50, 0.70), 0.20)

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
