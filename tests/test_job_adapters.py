import unittest

from app.job_adapters import adapt_publish_job, adapt_reply_job


class FakeModule:
    def __init__(self):
        self.fb_responses = []
        self.get_responses = []
        self.gemini_responses = []

    def call_fb_api(self, endpoint, data, files=None):
        return self.fb_responses.pop(0)

    def get_fb_api(self, endpoint, params=None):
        return self.get_responses.pop(0)

    def call_gemini(self, prompt, timeout=30):
        return self.gemini_responses.pop(0)


class JobAdapterTests(unittest.TestCase):
    def test_publish_success_when_primary_endpoint_succeeds(self):
        module = FakeModule()
        module.fb_responses = [(200, {"id": "post-1"})]

        def legacy_job():
            module.call_fb_api("me/feed", {"message": "hello"})

        job = adapt_publish_job(legacy_job, module, lambda endpoint: endpoint == "me/feed")
        outcome = job()

        self.assertEqual(outcome.status, "success")

    def test_primary_publish_failure_becomes_exception(self):
        module = FakeModule()
        module.fb_responses = [(500, {"error": "facebook down"})]

        def legacy_job():
            module.call_fb_api("me/feed", {"message": "hello"})

        job = adapt_publish_job(legacy_job, module, lambda endpoint: endpoint == "me/feed")
        with self.assertRaisesRegex(RuntimeError, "primary Facebook publish failed"):
            job()

    def test_optional_comment_failure_after_primary_success_does_not_fail_job(self):
        module = FakeModule()
        module.fb_responses = [
            (200, {"id": "post-1"}),
            (500, {"error": "comment failed"}),
        ]

        def legacy_job():
            module.call_fb_api("me/feed", {"message": "hello"})
            module.call_fb_api("post-1/comments", {"message": "seed"})

        job = adapt_publish_job(legacy_job, module, lambda endpoint: endpoint == "me/feed")
        outcome = job()

        self.assertEqual(outcome.status, "success")

    def test_no_publish_can_be_explicitly_skipped(self):
        module = FakeModule()
        job = adapt_publish_job(lambda: None, module, lambda endpoint: endpoint == "me/feed", allow_skip=True)

        outcome = job()

        self.assertEqual(outcome.status, "skipped")

    def test_missing_publish_is_failure_when_job_is_expected_to_post(self):
        module = FakeModule()
        job = adapt_publish_job(lambda: None, module, lambda endpoint: endpoint == "me/feed", allow_skip=False)

        with self.assertRaisesRegex(RuntimeError, "completed without a primary publish"):
            job()

    def test_gemini_failure_is_not_misclassified_as_skip(self):
        module = FakeModule()
        module.gemini_responses = [None]

        def legacy_job():
            module.call_gemini("write post")

        job = adapt_publish_job(legacy_job, module, lambda endpoint: endpoint == "me/feed", allow_skip=True)
        with self.assertRaisesRegex(RuntimeError, "Gemini returned no content"):
            job()

    def test_reply_facebook_read_failure_becomes_exception(self):
        module = FakeModule()
        module.get_responses = [(500, {"error": "graph down"})]

        def legacy_reply():
            module.get_fb_api("me")

        job = adapt_reply_job(legacy_reply, module)
        with self.assertRaisesRegex(RuntimeError, "Facebook reply API failed"):
            job()

    def test_reply_with_no_comments_can_complete_successfully(self):
        module = FakeModule()
        module.get_responses = [(200, {"id": "page"}), (200, {"data": []})]

        def legacy_reply():
            module.get_fb_api("me")
            module.get_fb_api("me/feed")

        outcome = adapt_reply_job(legacy_reply, module)()
        self.assertEqual(outcome.status, "success")


if __name__ == "__main__":
    unittest.main()
