import unittest
from datetime import datetime
from unittest.mock import patch

import app.db as db
from app.content_models import ContentCandidate
from app.learning_repository import load_learning_observations
from app.publication_ledger import record_published_content
from app.style_steering import StyleTarget, restyle_candidate


class StyleExperimentTrackingTests(unittest.TestCase):
    def test_content_candidate_supports_separate_experiment_key(self):
        candidate = ContentCandidate(
            category="fun",
            topic_key="topic",
            topic_text="text",
            content_text="text",
            style_experiment_key="tone:witty-short-punchline",
        )
        self.assertEqual(candidate.style_experiment_key, "tone:witty-short-punchline")
        self.assertEqual(candidate.style_type, "unknown")

    def test_successful_restyle_carries_experiment_key_without_faking_observed_style(self):
        candidate = ContentCandidate(
            category="fun",
            topic_key="topic",
            topic_text="Bản gốc",
            content_text="Bản gốc",
        )
        target = StyleTarget(
            hook_type=None,
            style_type="witty-short-punchline",
            cta_type=None,
            mode="explore",
            experiment_key="tone:witty-short-punchline",
        )

        rewritten = restyle_candidate(candidate, target, lambda _prompt: "Bản viết lại")

        self.assertEqual(rewritten.style_experiment_key, "tone:witty-short-punchline")
        self.assertEqual(rewritten.style_type, "unknown")
        self.assertEqual(rewritten.content_text, "Bản viết lại")

    def test_failed_restyle_does_not_claim_experiment_exposure(self):
        candidate = ContentCandidate(
            category="finance",
            topic_key="topic",
            topic_text="Bản gốc",
            content_text="Bản gốc",
        )
        target = StyleTarget(
            hook_type=None,
            style_type="explanatory-contrast",
            cta_type=None,
            mode="explore",
            experiment_key="tone:explanatory-contrast",
        )

        rewritten = restyle_candidate(candidate, target, lambda _prompt: None)

        self.assertIsNone(rewritten.style_experiment_key)

    def test_schema_adds_experiment_column_for_existing_content_posts(self):
        calls = []

        def fake_execute(query, params=()):
            calls.append((query, params))
            if "PRAGMA table_info(job_runs)" in query:
                return [
                    (0, "run_key", "TEXT", 0, None, 1),
                    (1, "scheduled_for", "TEXT", 0, None, 0),
                    (2, "delay_minutes", "INTEGER", 0, None, 0),
                ]
            if "PRAGMA table_info(content_posts)" in query:
                return [
                    (0, "id", "INTEGER", 0, None, 1),
                    (1, "style_type", "TEXT", 1, "unknown", 0),
                ]
            return []

        with patch.object(db, "execute", side_effect=fake_execute):
            db.ensure_schema()

        queries = [query for query, _ in calls]
        self.assertTrue(any("ADD COLUMN style_experiment_key TEXT" in query for query in queries))
        schema = "\n".join(
            query for query in queries if "CREATE TABLE IF NOT EXISTS content_posts" in query
        )
        self.assertIn("style_experiment_key", schema)

    @patch("app.publication_ledger.content_repository.record_candidate")
    def test_publication_ledger_persists_experiment_separately_from_observed_style(self, record):
        candidate = ContentCandidate(
            category="fun",
            topic_key="topic",
            topic_text="Final",
            content_text="Final",
            hook_type="question",
            style_type="witty",
            cta_type="choose_side",
            style_experiment_key="tone:witty-short-punchline",
        )
        intelligence = type(
            "Intelligence",
            (),
            {"candidate": candidate, "quality_score": 82.0, "duplicate_score": 0.1},
        )()

        record.return_value = 7
        result = record_published_content(
            lambda *_: [],
            action="fun",
            endpoint="me/feed",
            request_data={"message": "Final"},
            response={"id": "post-7"},
            intelligence=intelligence,
        )

        self.assertEqual(result, 7)
        stored_candidate = record.call_args.args[1]
        self.assertEqual(stored_candidate.style_type, "witty")
        self.assertEqual(stored_candidate.style_experiment_key, "tone:witty-short-punchline")

    def test_learning_observation_exposes_experiment_key_for_lifecycle_scoring(self):
        def execute(query, params=()):
            if "FROM content_posts cp" in query:
                return [
                    (
                        "post-1", "fun", "2026-08-30T13:00:00+00:00",
                        "question", "witty", "choose_side", "text",
                        81.0, "final", "2026-08-30T13:02:00+00:00",
                        "tone:witty-short-punchline",
                    )
                ]
            return []

        rows = load_learning_observations(
            execute,
            now=datetime.fromisoformat("2026-08-31T00:00:00+00:00"),
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].style_experiment_key, "tone:witty-short-punchline")
        self.assertEqual(rows[0].style_type, "witty")


if __name__ == "__main__":
    unittest.main()
