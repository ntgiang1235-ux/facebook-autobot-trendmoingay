from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def test_ci_runs_full_test_and_compile_gate(self):
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("python -m unittest discover -s tests -v", ci)
        self.assertIn("python -m compileall -q .", ci)

    def test_production_workflow_does_not_rerun_unit_tests(self):
        prod = (ROOT / ".github/workflows/facebook-autobot.yml").read_text(encoding="utf-8")
        self.assertNotIn("python -m unittest discover -s tests -v", prod)
        self.assertNotIn("Run unit tests", prod)

    def test_production_workflow_uses_planner_and_half_hour_dispatcher(self):
        prod = (ROOT / ".github/workflows/facebook-autobot.yml").read_text(encoding="utf-8")

        self.assertIn('cron: "47 0 * * *"', prod)
        self.assertIn('cron: "7,37 * * * *"', prod)
        self.assertIn('"47 0 * * *") ACTION="planner"', prod)
        self.assertIn('"7,37 * * * *") ACTION="dispatch"', prod)
        self.assertIn("SCHEDULED_CRON:", prod)

        for action in ("planner", "dispatch", "metrics"):
            self.assertIn(f"          - {action}", prod)

    def test_operational_jobs_keep_fixed_crons_and_metrics_collection(self):
        prod = (ROOT / ".github/workflows/facebook-autobot.yml").read_text(encoding="utf-8")

        expected = {
            '17 0 * * *': 'health',
            '15 1 * * *': 'reply',
            '15 7 * * *': 'reply',
            '15 13 * * *': 'reply',
            '30 14 * * *': 'summary',
            '17 3 * * *': 'metrics',
            '17 9 * * *': 'metrics',
            '17 15 * * *': 'metrics',
            '17 21 * * *': 'metrics',
        }
        for cron, action in expected.items():
            self.assertIn(f'cron: "{cron}"', prod)
            self.assertIn(f'"{cron}") ACTION="{action}"', prod)

    def test_fixed_content_crons_are_removed_after_dispatcher_cutover(self):
        prod = (ROOT / ".github/workflows/facebook-autobot.yml").read_text(encoding="utf-8")

        old_content_crons = (
            "30 1 * * *",
            "30 4 * * *",
            "30 7 * * *",
            "30 11 * * *",
            "7 2 * * *",
            "7 4 * * *",
            "30 6 * * *",
            "7 13 * * *",
            "7 9 * * *",
            "30 2 * * *",
            "45 5 * * *",
            "30 10 * * *",
            "7 14 * * *",
        )
        for cron in old_content_crons:
            self.assertNotIn(f'cron: "{cron}"', prod)


if __name__ == "__main__":
    unittest.main()
