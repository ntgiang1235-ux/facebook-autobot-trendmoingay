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

    def test_production_workflow_avoids_top_of_hour_and_adds_health_check(self):
        prod = (ROOT / ".github/workflows/facebook-autobot.yml").read_text(encoding="utf-8")

        for cron in ("7 2 * * *", "7 4 * * *", "7 9 * * *", "7 13 * * *", "7 14 * * *"):
            self.assertIn(f'cron: "{cron}"', prod)
        self.assertIn('cron: "17 0 * * *"', prod)
        self.assertIn('ACTION="health"', prod)
        self.assertIn("SCHEDULED_CRON:", prod)

        for old_cron in ("0 2 * * *", "0 4 * * *", "0 9 * * *", "0 13 * * *", "0 14 * * *"):
            self.assertNotIn(f'cron: "{old_cron}"', prod)


if __name__ == "__main__":
    unittest.main()
