import unittest


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, base_payload, insights=None, base_status=200):
        self.base_payload = base_payload
        self.insights = insights or {}
        self.base_status = base_status
        self.calls = []

    def get(self, url, params=None, timeout=None):
        params = params or {}
        self.calls.append((url, params, timeout))
        if url.endswith("/insights"):
            metric = params.get("metric")
            value = self.insights.get(metric)
            if isinstance(value, tuple):
                payload, status = value
                return FakeResponse(payload, status)
            if value is None:
                return FakeResponse({"error": {"message": "metric unavailable"}}, 400)
            return FakeResponse({"data": [{"name": metric, "values": [{"value": value}]}]})
        return FakeResponse(self.base_payload, self.base_status)


class FacebookMetricsTests(unittest.TestCase):
    def engagement_payload(self):
        return {
            "reactions": {"summary": {"total_count": 12}},
            "comments": {"summary": {"total_count": 4}},
            "shares": {"count": 3},
        }

    def test_collects_basic_counts_and_available_insights(self):
        from app.facebook_metrics import collect_post_metrics

        http = FakeSession(
            self.engagement_payload(),
            {
                "post_impressions_unique": 1000,
                "post_impressions": 1200,
                "post_video_views": 300,
            },
        )

        metrics = collect_post_metrics(http, "post-1", "token")

        self.assertEqual(metrics.reactions, 12)
        self.assertEqual(metrics.comments, 4)
        self.assertEqual(metrics.shares, 3)
        self.assertEqual(metrics.reach, 1000)
        self.assertEqual(metrics.impressions, 1200)
        self.assertEqual(metrics.video_views, 300)
        self.assertIsNone(metrics.follower_delta)
        self.assertTrue({"reactions", "comments", "shares", "reach", "impressions"}.issubset(metrics.capabilities))

    def test_denied_insight_does_not_erase_basic_engagement(self):
        from app.facebook_metrics import collect_post_metrics

        http = FakeSession(
            self.engagement_payload(),
            {
                "post_impressions_unique": ({"error": {"message": "denied"}}, 403),
                "post_impressions": 900,
            },
        )

        metrics = collect_post_metrics(http, "post-2", "token")

        self.assertEqual((metrics.reactions, metrics.comments, metrics.shares), (12, 4, 3))
        self.assertIsNone(metrics.reach)
        self.assertEqual(metrics.impressions, 900)
        self.assertNotIn("reach", metrics.capabilities)
        self.assertIn("impressions", metrics.capabilities)

    def test_only_engagement_counts_is_valid_capability_set(self):
        from app.facebook_metrics import collect_post_metrics

        metrics = collect_post_metrics(FakeSession(self.engagement_payload()), "post-3", "token")

        self.assertEqual(metrics.capabilities, frozenset({"reactions", "comments", "shares"}))
        self.assertIsNone(metrics.reach)
        self.assertIsNone(metrics.impressions)
        self.assertIsNone(metrics.video_views)

    def test_missing_optional_basic_field_is_not_fabricated_as_zero(self):
        from app.facebook_metrics import collect_post_metrics

        payload = self.engagement_payload()
        payload.pop("shares")
        metrics = collect_post_metrics(FakeSession(payload), "post-4", "token")

        self.assertIsNone(metrics.shares)
        self.assertNotIn("shares", metrics.capabilities)

    def test_graph_http_error_on_basic_post_fields_raises(self):
        from app.facebook_metrics import FacebookMetricsError, collect_post_metrics

        http = FakeSession({"error": {"message": "expired token"}}, base_status=401)
        with self.assertRaises(FacebookMetricsError):
            collect_post_metrics(http, "post-5", "token")


if __name__ == "__main__":
    unittest.main()
