import unittest
from pathlib import Path


class ReadinessWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.readiness_path = Path(".github/workflows/production-readiness.yml")
        self.production_path = Path(".github/workflows/facebook-autobot.yml")

    def test_readiness_workflow_is_manual_only(self):
        text = self.readiness_path.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("schedule:", text)

    def test_readiness_workflow_has_read_only_permissions(self):
        text = self.readiness_path.read_text(encoding="utf-8")
        self.assertIn("permissions:", text)
        self.assertIn("contents: read", text)
        self.assertNotIn("contents: write", text)

    def test_readiness_workflow_only_exposes_turso_secrets(self):
        text = self.readiness_path.read_text(encoding="utf-8")
        self.assertIn("TURSO_DATABASE_URL", text)
        self.assertIn("TURSO_AUTH_TOKEN", text)
        for forbidden in (
            "FB_ACCESS_TOKEN",
            "FACEBOOK",
            "GEMINI",
            "PEXELS",
            "TELEGRAM",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_readiness_workflow_runs_standalone_runner(self):
        text = self.readiness_path.read_text(encoding="utf-8")
        self.assertIn("python readiness_runner.py", text)
        self.assertNotIn("hardening_runner.py", text)
        self.assertNotIn("ensure_schema", text)

    def test_readiness_workflow_uses_repo_pinned_action_shas(self):
        text = self.readiness_path.read_text(encoding="utf-8")
        self.assertIn(
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            text,
        )
        self.assertIn(
            "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
            text,
        )
        self.assertNotIn("actions/checkout@v", text)
        self.assertNotIn("actions/setup-python@v", text)

    def test_production_workflow_remains_separate_from_readiness(self):
        text = self.production_path.read_text(encoding="utf-8")
        self.assertNotIn("readiness_runner.py", text)
        self.assertNotIn("phase4_verify", text)
        self.assertNotIn("production-readiness", text)


if __name__ == "__main__":
    unittest.main()
