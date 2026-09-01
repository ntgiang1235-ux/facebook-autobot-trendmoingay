from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "external-dispatch-wake.yml"


class ExternalDispatchWakeWorkflowTest(unittest.TestCase):
    def workflow_text(self) -> str:
        self.assertTrue(WORKFLOW.exists(), "external dispatch wake workflow must exist")
        return WORKFLOW.read_text(encoding="utf-8")

    def test_external_wake_is_manual_dispatch_only(self):
        text = self.workflow_text()
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("schedule:", text)

    def test_external_wake_has_isolated_non_cancelling_concurrency(self):
        text = self.workflow_text()
        self.assertIn("group: facebook-autobot-external-dispatch-wake", text)
        self.assertIn("cancel-in-progress: false", text)

    def test_external_wake_runs_only_dispatch(self):
        text = self.workflow_text()
        self.assertIn('run: python hardening_runner.py "dispatch"', text)
        self.assertNotIn('hardening_runner.py "planner"', text)
        self.assertNotIn("ACTION:", text)
        self.assertNotIn("inputs:", text)

    def test_external_wake_does_not_inherit_github_schedule_staleness_gate(self):
        text = self.workflow_text()
        self.assertNotIn("SCHEDULED_CRON", text)

    def test_external_wake_wires_existing_runtime_secrets(self):
        text = self.workflow_text()
        required = (
            "GEMINI_API_KEYS",
            "FB_ACCESS_TOKEN",
            "FB_PAGE_ID",
            "TELEGRAM_TOKEN",
            "TELEGRAM_CHAT_ID",
            "BLACKLIST_WORDS",
            "AFFILIATE_LINK",
            "PEXELS_API_KEY",
            "TURSO_DATABASE_URL",
            "TURSO_AUTH_TOKEN",
        )
        for secret in required:
            self.assertIn(f"{secret}: ${{{{ secrets.{secret} }}}}", text)


if __name__ == "__main__":
    unittest.main()
