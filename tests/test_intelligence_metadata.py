import unittest
from types import SimpleNamespace
from unittest.mock import patch

import hardening_runner
from app.content_models import ContentCandidate
from app.job_adapters import adapt_publish_job
from app import publication_ledger


class FakeModule:
    def __init__(self):
        self.call_gemini = lambda prompt, timeout=30: "ok"
        self.send_tele = lambda message: True
        self.calls = []

    def call_fb_api(self, endpoint, data, files=None):
        self.calls.append((endpoint, dict(data)))
        return 200, {"id": "post-1"}


class IntelligenceMetadataTests(unittest.TestCase):
    def _decision(self):
        candidate = ContentCandidate(
            category="finance",
            topic_key="finance:key",
            topic_text="Final rewritten topic",
            content_text="Final rewritten content",
            source_url="https://example.com/story",
            hook_type="number",
            style_type="explanatory",
            cta_type="opinion_question",
            format_type="text",
        )
        return SimpleNamespace(
            publish=True,
            status="ready",
            request_data={
                "message": candidate.content_text,
                "link": candidate.source_url,
            },
            quality_score=86.5,
            duplicate_score=0.12,
            rewrite_count=1,
            detail="ready",
            candidate=candidate,
        )

    def test_adapter_passes_accepted_decision_to_intelligence_callback(self):
        module = FakeModule()
        decision = self._decision()
        captured = []

        def legacy_job():
            module.call_fb_api(
                "me/feed",
                {"message": "original", "link": "https://example.com/story"},
            )

        outcome = adapt_publish_job(
            legacy_job,
            module,
            lambda endpoint: endpoint == "me/feed",
            before_publish=lambda endpoint, request: decision,
            on_published_intelligence=lambda endpoint, request, response, accepted: captured.append(
                (endpoint, request, response, accepted)
            ),
        )()

        self.assertEqual(outcome.status, "success")
        self.assertEqual(len(captured), 1)
        self.assertIs(captured[0][3], decision)
        self.assertEqual(captured[0][1]["message"], "Final rewritten content")

    def test_publication_ledger_persists_final_candidate_and_gate_scores(self):
        decision = self._decision()
        with patch("app.publication_ledger.content_repository.record_candidate") as record:
            record.return_value = 9
            result = publication_ledger.record_published_content(
                lambda *args, **kwargs: [],
                action="finance",
                endpoint="me/feed",
                request_data=decision.request_data,
                response={"id": "post-1"},
                intelligence=decision,
            )

        self.assertEqual(result, 9)
        candidate = record.call_args.args[1]
        metadata = record.call_args.kwargs
        self.assertEqual(candidate.content_text, "Final rewritten content")
        self.assertEqual(candidate.hook_type, "number")
        self.assertEqual(candidate.style_type, "explanatory")
        self.assertEqual(candidate.cta_type, "opinion_question")
        self.assertEqual(metadata["quality_score"], 86.5)
        self.assertEqual(metadata["duplicate_score"], 0.12)

    def test_hardening_intelligence_callback_forwards_decision_to_ledger(self):
        decision = self._decision()
        callback = hardening_runner._adaptive_publish_intelligence_callback("finance")
        with patch("hardening_runner.publication_ledger.record_published_content") as record:
            callback(
                "me/feed",
                decision.request_data,
                {"id": "post-1"},
                decision,
            )

        self.assertEqual(record.call_args.kwargs["action"], "finance")
        self.assertIs(record.call_args.kwargs["intelligence"], decision)


if __name__ == "__main__":
    unittest.main()
