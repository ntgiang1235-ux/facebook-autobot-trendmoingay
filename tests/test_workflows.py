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


if __name__ == "__main__":
    unittest.main()
