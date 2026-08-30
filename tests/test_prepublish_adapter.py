import unittest

from app.job_adapters import adapt_publish_job
from app.prepublish_guard import PrePublishDecision


class RecordingModule:
    def __init__(self):
        self.fb_calls = []
        self.delivery_calls = []

    def call_fb_api(self, endpoint, data, files=None):
        self.fb_calls.append((endpoint, dict(data), files))
        return 200, {"id": "real-post-1"}

    def call_gemini(self, prompt, timeout=30):
        return "ok"

    def send_tele(self, message):
        self.delivery_calls.append(message)
        return True


class PrePublishAdapterTests(unittest.TestCase):
    def test_ready_decision_rewrites_request_before_real_facebook_call(self):
        module = RecordingModule()
        published = []

        def legacy_job():
            module.call_fb_api("me/feed", {"message": "draft", "link": "https://example.com"})

        decision = PrePublishDecision(
            publish=True,
            status="ready",
            request_data={"message": "rewritten", "link": "https://example.com"},
            quality_score=82.0,
            duplicate_score=0.2,
            rewrite_count=1,
            detail="good",
        )
        outcome = adapt_publish_job(
            legacy_job,
            module,
            lambda endpoint: endpoint == "me/feed",
            before_publish=lambda endpoint, request: decision,
            on_published=lambda endpoint, request, payload: published.append((endpoint, request, payload)),
        )()

        self.assertEqual(outcome.status, "success")
        self.assertEqual(module.fb_calls[0][1]["message"], "rewritten")
        self.assertEqual(module.fb_calls[0][1]["link"], "https://example.com")
        self.assertEqual(published[0][1]["message"], "rewritten")

    def test_rejected_decision_skips_real_facebook_followups_and_success_telegram(self):
        module = RecordingModule()

        def legacy_job():
            code, payload = module.call_fb_api("me/photos", {"message": "duplicate"}, files={"source": object()})
            if code == 200:
                module.call_fb_api(f"{payload['id']}/comments", {"message": "seed"})
                module.send_tele("published")

        decision = PrePublishDecision(
            publish=False,
            status="rejected_duplicate",
            request_data=None,
            quality_score=None,
            duplicate_score=1.0,
            rewrite_count=0,
            detail="same recent topic",
        )
        outcome = adapt_publish_job(
            legacy_job,
            module,
            lambda endpoint: endpoint == "me/photos",
            allow_skip=True,
            before_publish=lambda endpoint, request: decision,
        )()

        self.assertEqual(outcome.status, "skipped")
        self.assertIn("same recent topic", outcome.detail)
        self.assertEqual(module.fb_calls, [])
        self.assertEqual(module.delivery_calls, [])

    def test_prepublish_exception_fails_closed_without_real_facebook_call(self):
        module = RecordingModule()

        def legacy_job():
            module.call_fb_api("me/feed", {"message": "draft"})

        def broken_guard(endpoint, request):
            raise RuntimeError("turso unavailable")

        outcome = adapt_publish_job(
            legacy_job,
            module,
            lambda endpoint: endpoint == "me/feed",
            allow_skip=True,
            before_publish=broken_guard,
        )()

        self.assertEqual(outcome.status, "skipped")
        self.assertIn("pre-publish", outcome.detail)
        self.assertEqual(module.fb_calls, [])


if __name__ == "__main__":
    unittest.main()
