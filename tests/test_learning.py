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


class WeightUpdateTests(unittest.TestCase):
    def test_four_mature_samples_are_insufficient_for_weight_change(self):
        from app.learning import propose_weight

        proposal = propose_weight(0.25, 90.0, [40.0, 50.0, 60.0, 70.0], 4)
        self.assertEqual(proposal.status, "insufficient_data")
        self.assertEqual(proposal.proposed_weight, 0.25)

    def test_strong_score_increases_but_never_more_than_twenty_percent(self):
        from app.learning import propose_weight

        proposal = propose_weight(0.25, 90.0, [40.0, 50.0, 60.0, 70.0, 80.0], 5)
        self.assertGreater(proposal.proposed_weight, 0.25)
        self.assertLessEqual(proposal.proposed_weight, 0.30)
        self.assertEqual(proposal.status, "adjusted")

    def test_weak_score_decreases_but_never_more_than_twenty_percent(self):
        from app.learning import propose_weight

        proposal = propose_weight(0.25, 20.0, [30.0, 40.0, 50.0, 60.0, 70.0], 5)
        self.assertLess(proposal.proposed_weight, 0.25)
        self.assertGreaterEqual(proposal.proposed_weight, 0.20)
        self.assertEqual(proposal.status, "adjusted")

    def test_equal_peer_scores_keep_weight_stable(self):
        from app.learning import propose_weight

        proposal = propose_weight(0.40, 50.0, [50.0] * 5, 5)
        self.assertEqual(proposal.proposed_weight, 0.40)
        self.assertEqual(proposal.status, "stable")

    def test_bounded_normalization_sums_to_one_without_breaking_daily_caps(self):
        from app.learning import normalize_bounded_weights

        current = {"a": 0.50, "b": 0.30, "c": 0.20}
        proposed = {"a": 0.60, "b": 0.24, "c": 0.18}
        normalized = normalize_bounded_weights(current, proposed)

        self.assertAlmostEqual(sum(normalized.values()), 1.0)
        for key, current_weight in current.items():
            self.assertGreaterEqual(normalized[key], current_weight * 0.8 - 1e-9)
            self.assertLessEqual(normalized[key], current_weight * 1.2 + 1e-9)
            self.assertGreater(normalized[key], 0.0)

    def test_bounded_normalization_excludes_inactive_values(self):
        from app.learning import normalize_bounded_weights

        current = {"active": 0.70, "sleeping": 0.30}
        proposed = {"active": 0.84, "sleeping": 0.24}
        normalized = normalize_bounded_weights(current, proposed, active_values={"active"})
        self.assertEqual(normalized, {"active": 1.0, "sleeping": 0.0})


class CategorySuspensionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 30, 7, 0, tzinfo=timezone.utc)

    def make_stat(self, **overrides):
        from app.strategy_models import StrategyStat

        values = dict(
            dimension="category",
            value="recipe",
            sample_count=6,
            weighted_score_14d=65.0,
            recent_score_7d=65.0,
            success_rate=0.60,
            current_weight=0.20,
            last_used_at=(self.now - timedelta(days=1)).isoformat(),
            status="active",
            cooldown_until=None,
            retest_after=None,
            updated_at=self.now.isoformat(),
        )
        values.update(overrides)
        return StrategyStat(**values)

    def test_fewer_than_five_mature_posts_cannot_suspend(self):
        from app.learning import evaluate_category_status

        decision = evaluate_category_status(
            self.make_stat(sample_count=4, weighted_score_14d=20.0, recent_score_7d=20.0),
            peer_median=60.0,
            now=self.now,
            auto_suspend_enabled=True,
        )

        self.assertEqual(decision.status, "insufficient_data")
        self.assertTrue(decision.normal_allocation_eligible)
        self.assertFalse(decision.retest_eligible)

    def test_one_bad_recent_period_does_not_suspend_when_14d_performance_is_not_persistently_weak(self):
        from app.learning import evaluate_category_status

        decision = evaluate_category_status(
            self.make_stat(weighted_score_14d=58.0, recent_score_7d=25.0, success_rate=0.55),
            peer_median=60.0,
            now=self.now,
            auto_suspend_enabled=True,
        )

        self.assertEqual(decision.status, "active")
        self.assertTrue(decision.normal_allocation_eligible)

    def test_persistently_weak_category_suspends_for_seven_days(self):
        from app.learning import evaluate_category_status

        decision = evaluate_category_status(
            self.make_stat(weighted_score_14d=30.0, recent_score_7d=32.0, success_rate=0.20),
            peer_median=60.0,
            now=self.now,
            auto_suspend_enabled=True,
        )

        self.assertEqual(decision.status, "suspended")
        self.assertFalse(decision.normal_allocation_eligible)
        self.assertFalse(decision.retest_eligible)
        self.assertEqual(decision.proposed_weight, 0.0)
        self.assertEqual(decision.retest_after, self.now + timedelta(days=7))

    def test_auto_suspend_kill_switch_prevents_new_suspension(self):
        from app.learning import evaluate_category_status

        decision = evaluate_category_status(
            self.make_stat(weighted_score_14d=20.0, recent_score_7d=20.0, success_rate=0.10),
            peer_median=60.0,
            now=self.now,
            auto_suspend_enabled=False,
        )

        self.assertEqual(decision.status, "active")
        self.assertTrue(decision.normal_allocation_eligible)

    def test_suspended_category_is_excluded_before_retest_date(self):
        from app.learning import evaluate_category_status

        retest_at = self.now + timedelta(days=2)
        decision = evaluate_category_status(
            self.make_stat(status="suspended", current_weight=0.0, retest_after=retest_at.isoformat()),
            peer_median=60.0,
            now=self.now,
            auto_suspend_enabled=True,
        )

        self.assertEqual(decision.status, "suspended")
        self.assertFalse(decision.normal_allocation_eligible)
        self.assertFalse(decision.retest_eligible)

    def test_at_retest_date_exactly_one_controlled_retest_becomes_eligible(self):
        from app.learning import evaluate_category_status

        retest_at = self.now - timedelta(minutes=1)
        decision = evaluate_category_status(
            self.make_stat(
                status="suspended",
                current_weight=0.0,
                retest_after=retest_at.isoformat(),
                last_used_at=(retest_at - timedelta(days=1)).isoformat(),
            ),
            peer_median=60.0,
            now=self.now,
            auto_suspend_enabled=True,
        )

        self.assertEqual(decision.status, "suspended")
        self.assertTrue(decision.retest_eligible)
        self.assertFalse(decision.normal_allocation_eligible)

    def test_strong_completed_retest_restores_category_at_low_weight(self):
        from app.learning import evaluate_category_status

        retest_at = self.now - timedelta(hours=2)
        decision = evaluate_category_status(
            self.make_stat(
                status="suspended",
                current_weight=0.0,
                retest_after=retest_at.isoformat(),
                last_used_at=(self.now - timedelta(minutes=30)).isoformat(),
                weighted_score_14d=55.0,
                recent_score_7d=58.0,
                success_rate=0.55,
                sample_count=7,
            ),
            peer_median=60.0,
            now=self.now,
            auto_suspend_enabled=True,
        )

        self.assertEqual(decision.status, "active")
        self.assertTrue(decision.normal_allocation_eligible)
        self.assertFalse(decision.retest_eligible)
        self.assertGreater(decision.proposed_weight, 0.0)
        self.assertLessEqual(decision.proposed_weight, 0.10)
        self.assertIsNone(decision.retest_after)

    def test_weak_completed_retest_renews_seven_day_suspension(self):
        from app.learning import evaluate_category_status

        retest_at = self.now - timedelta(hours=2)
        decision = evaluate_category_status(
            self.make_stat(
                status="suspended",
                current_weight=0.0,
                retest_after=retest_at.isoformat(),
                last_used_at=(self.now - timedelta(minutes=30)).isoformat(),
                weighted_score_14d=30.0,
                recent_score_7d=25.0,
                success_rate=0.20,
                sample_count=7,
            ),
            peer_median=60.0,
            now=self.now,
            auto_suspend_enabled=True,
        )

        self.assertEqual(decision.status, "suspended")
        self.assertFalse(decision.retest_eligible)
        self.assertEqual(decision.proposed_weight, 0.0)
        self.assertEqual(decision.retest_after, self.now + timedelta(days=7))


if __name__ == "__main__":
    unittest.main()
