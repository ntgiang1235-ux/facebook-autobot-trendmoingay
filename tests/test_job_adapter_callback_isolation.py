import unittest
from unittest.mock import Mock

from app.job_adapters import adapt_publish_job


class FakeModule:
    def __init__(self):
        self.call_fb_api = Mock(
            side_effect=[
                (200, {"id": "post-1"}),
                (200, {"id": "post-2"}),
            ]
        )
        self.call_gemini = Mock(return_value="content")


class PublishCallbackIsolationTests(unittest.TestCase):
    def test_ledger_failure_after_facebook_success_cannot_trigger_legacy_fallback_publish(self):
        module = FakeModule()
        callback = Mock(side_effect=RuntimeError("ledger unavailable"))

        def legacy_job():
            try:
                module.call_fb_api("me/photos", {"message": "primary"})
            except Exception:
                # Legacy image helpers may interpret any exception as an upload
                # failure and retry as a text post. A ledger error must never be
                # raised inside the Facebook call or this becomes a double post.
                module.call_fb_api("me/feed", {"message": "fallback"})

        job = adapt_publish_job(
            legacy_job,
            module,
            lambda endpoint: endpoint in {"me/photos", "me/feed"},
            on_published=callback,
        )

        with self.assertRaisesRegex(RuntimeError, "ledger unavailable"):
            job()

        self.assertEqual(module.call_fb_api.call_count, 1)
        callback.assert_called_once_with(
            "me/photos",
            {"message": "primary"},
            {"id": "post-1"},
        )


if __name__ == "__main__":
    unittest.main()
