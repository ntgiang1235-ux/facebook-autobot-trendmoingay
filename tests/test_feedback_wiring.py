from pathlib import Path
import unittest
from unittest.mock import patch

import hardening_runner


ROOT = Path(__file__).resolve().parents[1]


class FeedbackLoopWiringTests(unittest.TestCase):
    def test_hardening_runner_exposes_learn_action_and_routes_shared_db(self):
        with patch.object(
            hardening_runner.feedback_loop,
            "refresh_strategy",
            return_value=object(),
        ) as refresh:
            jobs = hardening_runner.resolve_jobs()
            result = jobs["learn"]()

        refresh.assert_called_once_with(hardening_runner.db.execute)
        self.assertIsNotNone(result)
        self.assertIn("learn", hardening_runner.VALID_ACTIONS)

    def test_production_workflow_runs_learning_before_daily_planner(self):
        prod = (ROOT / ".github/workflows/facebook-autobot.yml").read_text(encoding="utf-8")

        self.assertIn("          - learn", prod)
        self.assertIn('cron: "27 0 * * *"', prod)
        self.assertIn('"27 0 * * *") ACTION="learn"', prod)
        self.assertIn('cron: "47 0 * * *"', prod)
        self.assertLess(prod.index('cron: "27 0 * * *"'), prod.index('cron: "47 0 * * *"'))


if __name__ == "__main__":
    unittest.main()
