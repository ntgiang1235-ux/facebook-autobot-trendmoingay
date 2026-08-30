import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from app.facebook_metrics import CollectedMetrics, FacebookMetricsError
from app.metrics_repository import DuePost
from app.scoring import ScoreResult, ScoringBaseline


class MetricsRunnerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        self.baseline = ScoringBaseline((), (), (), (), (), ())

    def metrics(self, **overrides):
        values = dict(
            reactions=10,
            comments=2,
            shares=1,
            reach=1000,
            impressions=1200,
            video_views=None,
            follower_delta=None,
            capabilities=frozenset({"reactions", "comments", "shares", "reach", "impressions"}),
        )
        values.update(overrides)
        return CollectedMetrics(**values)

    def test_collects_24h_and_72h_due_posts_and_saves_canonical_snapshots(self):
        import metrics_runner

        due = [
            DuePost(1, "post-early", "2026-08-29T11:00:00+00:00", "early"),
            DuePost(2, "post-final", "2026-08-27T10:00:00+00:00", "final"),
        ]
        saved = []
        with patch.object(metrics_runner.db, "ensure_schema") as ensure_schema, patch.object(
            metrics_runner, "due_posts", return_value=due
        ), patch.object(
            metrics_runner, "load_scoring_baseline", return_value=self.baseline
        ), patch.object(
            metrics_runner, "collect_post_metrics", return_value=self.metrics()
        ), patch.object(
            metrics_runner, "score_content", return_value=ScoreResult(72.5, "insufficient_baseline", {"engagement": 50.0})
        ), patch.object(
            metrics_runner, "save_snapshot", side_effect=lambda execute, snapshot: saved.append(snapshot)
        ), patch.object(metrics_runner.autobot, "FB_ACCESS_TOKEN", "token"):
            result = metrics_runner.collect_due_metrics(now=self.now)

        ensure_schema.assert_called_once()
        self.assertEqual(result, {"due": 2, "processed": 2, "failed": 0})
        self.assertEqual([item.score_kind for item in saved], ["early", "final"])
        self.assertEqual([item.facebook_post_id for item in saved], ["post-early", "post-final"])
        self.assertEqual(saved[0].content_score, 72.5)
        self.assertGreaterEqual(saved[0].age_hours, 24)
        self.assertGreaterEqual(saved[1].age_hours, 72)

    def test_partial_insights_are_saved_as_none_not_zero(self):
        import metrics_runner

        due = [DuePost(1, "post-1", "2026-08-29T11:00:00+00:00", "early")]
        collected = self.metrics(reach=None, impressions=900, capabilities=frozenset({"reactions", "comments", "shares", "impressions"}))
        saved = []
        with patch.object(metrics_runner.db, "ensure_schema"), patch.object(
            metrics_runner, "due_posts", return_value=due
        ), patch.object(metrics_runner, "load_scoring_baseline", return_value=self.baseline), patch.object(
            metrics_runner, "collect_post_metrics", return_value=collected
        ), patch.object(
            metrics_runner, "score_content", return_value=ScoreResult(50.0, "insufficient_baseline", {})
        ), patch.object(
            metrics_runner, "save_snapshot", side_effect=lambda execute, snapshot: saved.append(snapshot)
        ), patch.object(metrics_runner.autobot, "FB_ACCESS_TOKEN", "token"):
            metrics_runner.collect_due_metrics(now=self.now)

        self.assertIsNone(saved[0].reach)
        self.assertEqual(saved[0].impressions, 900)
        self.assertNotIn("reach", saved[0].metric_capabilities)

    def test_one_graph_failure_continues_remaining_due_posts(self):
        import metrics_runner

        due = [
            DuePost(1, "bad", "2026-08-29T11:00:00+00:00", "early"),
            DuePost(2, "good", "2026-08-29T10:00:00+00:00", "early"),
        ]
        collect = Mock(side_effect=[FacebookMetricsError("upstream"), self.metrics()])
        with patch.object(metrics_runner.db, "ensure_schema"), patch.object(
            metrics_runner, "due_posts", return_value=due
        ), patch.object(metrics_runner, "load_scoring_baseline", return_value=self.baseline), patch.object(
            metrics_runner, "collect_post_metrics", collect
        ), patch.object(
            metrics_runner, "score_content", return_value=ScoreResult(50.0, "insufficient_baseline", {})
        ), patch.object(metrics_runner, "save_snapshot") as save, patch.object(
            metrics_runner.notifications, "send_failure"
        ) as notify, patch.object(metrics_runner.autobot, "FB_ACCESS_TOKEN", "token"):
            result = metrics_runner.collect_due_metrics(now=self.now)

        self.assertEqual(result, {"due": 2, "processed": 1, "failed": 1})
        save.assert_called_once()
        notify.assert_called_once()
        self.assertIn("bad", notify.call_args.args[0])

    def test_all_due_posts_failing_marks_systemic_failure(self):
        import metrics_runner

        due = [DuePost(1, "bad", "2026-08-29T11:00:00+00:00", "early")]
        with patch.object(metrics_runner.db, "ensure_schema"), patch.object(
            metrics_runner, "due_posts", return_value=due
        ), patch.object(metrics_runner, "load_scoring_baseline", return_value=self.baseline), patch.object(
            metrics_runner, "collect_post_metrics", side_effect=FacebookMetricsError("expired token")
        ), patch.object(metrics_runner.notifications, "send_failure"), patch.object(
            metrics_runner.autobot, "FB_ACCESS_TOKEN", "token"
        ):
            with self.assertRaisesRegex(RuntimeError, "không xử lý được post nào"):
                metrics_runner.collect_due_metrics(now=self.now)

    def test_unexpected_local_failure_is_not_swallowed_as_partial_api_failure(self):
        import metrics_runner

        due = [
            DuePost(1, "post-1", "2026-08-29T11:00:00+00:00", "early"),
            DuePost(2, "post-2", "2026-08-29T10:00:00+00:00", "early"),
        ]
        with patch.object(metrics_runner.db, "ensure_schema"), patch.object(
            metrics_runner, "due_posts", return_value=due
        ), patch.object(metrics_runner, "load_scoring_baseline", return_value=self.baseline), patch.object(
            metrics_runner, "collect_post_metrics", return_value=self.metrics()
        ), patch.object(
            metrics_runner, "score_content", return_value=ScoreResult(50.0, "insufficient_baseline", {})
        ), patch.object(
            metrics_runner, "save_snapshot", side_effect=RuntimeError("database programming error")
        ), patch.object(metrics_runner.notifications, "send_failure") as notify, patch.object(
            metrics_runner.autobot, "FB_ACCESS_TOKEN", "token"
        ):
            with self.assertRaisesRegex(RuntimeError, "database programming error"):
                metrics_runner.collect_due_metrics(now=self.now)

        notify.assert_not_called()

    def test_no_due_posts_is_successful_noop(self):
        import metrics_runner

        with patch.object(metrics_runner.db, "ensure_schema"), patch.object(
            metrics_runner, "due_posts", return_value=[]
        ), patch.object(metrics_runner.autobot, "FB_ACCESS_TOKEN", "token"):
            result = metrics_runner.collect_due_metrics(now=self.now)

        self.assertEqual(result, {"due": 0, "processed": 0, "failed": 0})


if __name__ == "__main__":
    unittest.main()
