import unittest
from datetime import datetime, timedelta, timezone


class LearningAggregationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 30, 7, 0, tzinfo=timezone.utc)

    def test_recency_weight_exact_boundaries(self):
        from app.learning import recency_weight

        expected = {
            0: 1.50,
            3: 1.50,
            4: 1.25,
            7: 1.25,
            8: 1.00,
            14: 1.00,
            15: 0.00,
        }
        for age_days, weight in expected.items():
            with self.subTest(age_days=age_days):
                self.assertEqual(recency_weight(age_days), weight)

    def test_final_sample_has_full_weight_and_early_has_half_weight(self):
        from app.learning import LearningSample, aggregate_dimension

        samples = [
            LearningSample(
                score=80.0,
                published_at=self.now - timedelta(days=1),
                score_kind="final",
            ),
            LearningSample(
                score=20.0,
                published_at=self.now - timedelta(days=1),
                score_kind="early",
            ),
        ]

        stat = aggregate_dimension(samples, self.now, success_baseline=50.0)

        # final weight = 1.50; early weight = 0.75
        self.assertAlmostEqual(stat.weighted_score_14d, 60.0)
        self.assertAlmostEqual(stat.recent_score_7d, 60.0)
        self.assertEqual(stat.mature_sample_count, 1)
        self.assertAlmostEqual(stat.success_rate, 1.5 / 2.25)

    def test_mature_sample_count_ignores_early_only_snapshots(self):
        from app.learning import LearningSample, aggregate_dimension

        samples = [
            LearningSample(70.0, self.now - timedelta(days=2), "early"),
            LearningSample(75.0, self.now - timedelta(days=5), "early"),
            LearningSample(65.0, self.now - timedelta(days=8), "final"),
            LearningSample(90.0, self.now - timedelta(days=15), "final"),
        ]

        stat = aggregate_dimension(samples, self.now, success_baseline=60.0)

        self.assertEqual(stat.mature_sample_count, 1)
        self.assertEqual(stat.included_sample_count, 3)

    def test_recent_score_7d_excludes_older_learning_window_samples(self):
        from app.learning import LearningSample, aggregate_dimension

        samples = [
            LearningSample(90.0, self.now - timedelta(days=2), "final"),
            LearningSample(30.0, self.now - timedelta(days=10), "final"),
        ]

        stat = aggregate_dimension(samples, self.now, success_baseline=50.0)

        self.assertAlmostEqual(stat.recent_score_7d, 90.0)
        self.assertLess(stat.weighted_score_14d, stat.recent_score_7d)
        self.assertEqual(stat.mature_sample_count, 2)

    def test_future_sample_and_unknown_score_kind_are_excluded(self):
        from app.learning import LearningSample, aggregate_dimension

        samples = [
            LearningSample(90.0, self.now + timedelta(hours=1), "final"),
            LearningSample(70.0, self.now - timedelta(days=1), "preview"),
        ]

        stat = aggregate_dimension(samples, self.now, success_baseline=50.0)

        self.assertEqual(stat.included_sample_count, 0)
        self.assertEqual(stat.mature_sample_count, 0)
        self.assertEqual(stat.weighted_score_14d, 50.0)
        self.assertEqual(stat.recent_score_7d, 50.0)
        self.assertEqual(stat.success_rate, 0.0)


if __name__ == "__main__":
    unittest.main()
