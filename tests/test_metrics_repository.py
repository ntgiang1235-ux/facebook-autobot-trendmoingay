import unittest


class MetricsRepositoryTests(unittest.TestCase):
    def test_save_snapshot_upserts_canonical_score_kind(self):
        from app.metrics_repository import MetricSnapshot, save_snapshot

        calls = []

        def execute(query, params=()):
            calls.append((query, params))
            return []

        snapshot = MetricSnapshot(
            facebook_post_id="post-1",
            measured_at="2026-08-30T00:00:00+00:00",
            age_hours=24,
            reactions=10,
            comments=3,
            shares=2,
            reach=1000,
            impressions=1200,
            video_views=None,
            follower_delta=None,
            engagement_rate=0.022,
            content_score=77.5,
            metric_capabilities=frozenset({"reactions", "comments", "shares", "reach", "impressions"}),
            score_kind="early",
        )

        save_snapshot(execute, snapshot)

        query, params = calls[0]
        self.assertIn("ON CONFLICT(facebook_post_id, score_kind) DO UPDATE", query)
        self.assertEqual(params[0], "post-1")
        self.assertEqual(params[-1], "early")
        self.assertIn('"reach"', params[-2])

    def test_save_snapshot_rejects_unknown_score_kind_before_sql(self):
        from app.metrics_repository import MetricSnapshot, save_snapshot

        snapshot = MetricSnapshot(
            facebook_post_id="post-1",
            measured_at="2026-08-30T00:00:00+00:00",
            age_hours=24,
            reactions=0,
            comments=0,
            shares=0,
            reach=None,
            impressions=None,
            video_views=None,
            follower_delta=None,
            engagement_rate=None,
            content_score=50.0,
            metric_capabilities=frozenset(),
            score_kind="preview",
        )

        with self.assertRaises(ValueError):
            save_snapshot(lambda *args: self.fail("SQL should not run"), snapshot)

    def test_due_posts_returns_missing_24h_and_72h_snapshots(self):
        from app.metrics_repository import due_posts

        calls = []

        def execute(query, params=()):
            calls.append((query, params))
            return [
                (1, "post-early", "2026-08-29T00:00:00+00:00", "early"),
                (2, "post-final", "2026-08-27T00:00:00+00:00", "final"),
            ]

        rows = due_posts(execute, "2026-08-30T12:00:00+00:00")

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].facebook_post_id, "post-early")
        self.assertEqual(rows[0].score_kind, "early")
        self.assertEqual(rows[1].score_kind, "final")
        query, params = calls[0]
        self.assertIn("content_posts", query)
        self.assertIn("content_metrics", query)
        self.assertIn("24", query)
        self.assertIn("72", query)
        self.assertEqual(params, ("2026-08-30T12:00:00+00:00",))

    def test_recent_final_scores_returns_numeric_scores_only(self):
        from app.metrics_repository import recent_final_scores

        def execute(query, params=()):
            self.assertIn("score_kind = 'final'", query)
            self.assertEqual(params, (10,))
            return [(81.0,), (74.5,), (None,)]

        self.assertEqual(recent_final_scores(execute, 10), [81.0, 74.5])


if __name__ == "__main__":
    unittest.main()
