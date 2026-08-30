import unittest

from app.facebook_metrics import CollectedMetrics


class ScoringTests(unittest.TestCase):
    def metrics(self, **overrides):
        values = dict(
            reactions=100,
            comments=0,
            shares=0,
            reach=1000,
            impressions=1200,
            video_views=None,
            follower_delta=5,
            capabilities=frozenset({"reactions", "comments", "shares", "reach", "impressions", "follower_delta"}),
        )
        values.update(overrides)
        return CollectedMetrics(**values)

    def baseline(self):
        from app.scoring import ScoringBaseline

        return ScoringBaseline(
            engagement_rates=(0.01, 0.02, 0.03, 0.04, 0.05),
            weighted_interactions=(100, 100, 100, 100, 100),
            reach=(1000, 1000, 1000, 1000, 1000),
            impressions=(1200, 1200, 1200, 1200, 1200),
            conversation=(0, 0, 0, 0, 0),
            follower_delta=(5, 5, 5, 5, 5),
        )

    def test_weighted_interactions(self):
        from app.scoring import weighted_interactions

        self.assertEqual(weighted_interactions(10, 3, 2), 22.0)

    def test_engagement_rate_prefers_reach_then_impressions(self):
        from app.scoring import engagement_rate

        self.assertAlmostEqual(engagement_rate(self.metrics()), 0.1)
        self.assertAlmostEqual(engagement_rate(self.metrics(reach=None)), 100 / 1200)
        self.assertIsNone(engagement_rate(self.metrics(reach=0, impressions=0)))
        self.assertIsNone(engagement_rate(self.metrics(reach=None, impressions=None)))

    def test_winsorize_caps_extreme_viral_outlier(self):
        from app.scoring import winsorize

        capped = winsorize(10000, [10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
        self.assertLess(capped, 10000)
        self.assertLessEqual(capped, 100)

    def test_full_metric_scoring_uses_35_30_20_15_mix(self):
        from app.scoring import score_content

        result = score_content(self.metrics(), self.baseline(), follower_available=True)

        self.assertEqual(result.maturity, "mature")
        self.assertEqual(result.components["engagement"], 100.0)
        self.assertEqual(result.components["exposure"], 50.0)
        self.assertEqual(result.components["conversation"], 50.0)
        self.assertEqual(result.components["follower"], 50.0)
        self.assertEqual(result.score, 67.5)

    def test_no_follower_scoring_uses_40_35_25_mix(self):
        from app.scoring import score_content

        result = score_content(
            self.metrics(follower_delta=None, capabilities=frozenset({"reactions", "comments", "shares", "reach", "impressions"})),
            self.baseline(),
            follower_available=False,
        )

        self.assertEqual(result.score, 70.0)
        self.assertNotIn("follower", result.components)

    def test_no_exposure_uses_engagement_only_60_40_mix(self):
        from app.scoring import ScoringBaseline, score_content

        baseline = ScoringBaseline(
            engagement_rates=(),
            weighted_interactions=(10, 20, 30, 40, 50),
            reach=(),
            impressions=(),
            conversation=(0, 0, 0, 0, 0),
            follower_delta=(),
        )
        result = score_content(
            self.metrics(
                reach=None,
                impressions=None,
                follower_delta=None,
                capabilities=frozenset({"reactions", "comments", "shares"}),
            ),
            baseline,
            follower_available=False,
        )

        self.assertEqual(result.components["interactions"], 100.0)
        self.assertEqual(result.components["conversation"], 50.0)
        self.assertEqual(result.score, 80.0)

    def test_insufficient_baseline_is_neutral_not_overfit(self):
        from app.scoring import ScoringBaseline, score_content

        baseline = ScoringBaseline(
            engagement_rates=(0.01, 0.02, 0.03, 0.04),
            weighted_interactions=(10, 20, 30, 40),
            reach=(100, 200, 300, 400),
            impressions=(100, 200, 300, 400),
            conversation=(1, 2, 3, 4),
            follower_delta=(0, 1, 2, 3),
        )
        result = score_content(self.metrics(), baseline, follower_available=True)

        self.assertEqual(result.maturity, "insufficient_baseline")
        self.assertEqual(result.score, 50.0)
        self.assertTrue(all(value == 50.0 for value in result.components.values()))


if __name__ == "__main__":
    unittest.main()
