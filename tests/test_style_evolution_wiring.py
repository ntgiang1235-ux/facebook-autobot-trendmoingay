import unittest
from pathlib import Path
from unittest.mock import patch

import hardening_runner


WORKFLOW = Path(".github/workflows/facebook-autobot.yml")


class StyleEvolutionWiringTests(unittest.TestCase):
    def test_hardening_runner_exposes_style_evolve_action_and_routes_shared_dependencies(self):
        result = object()
        with patch(
            "hardening_runner.style_evolution.generate_next_experiment",
            return_value=result,
        ) as evolve:
            jobs = hardening_runner.resolve_jobs()
            self.assertIn("style_evolve", hardening_runner.VALID_ACTIONS)
            self.assertIs(jobs["style_evolve"](), result)

        evolve.assert_called_once_with(
            hardening_runner.db.execute,
            hardening_runner.autobot.call_gemini,
        )

    def test_workflow_runs_weekly_style_evolution_between_learning_and_planner(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("          - style_evolve", text)
        self.assertIn('- cron: "37 0 * * 0"', text)
        self.assertIn('"37 0 * * 0") ACTION="style_evolve" ;;', text)

        learn = text.index('- cron: "27 0 * * *"')
        evolve = text.index('- cron: "37 0 * * 0"')
        planner = text.index('- cron: "47 0 * * *"')
        self.assertLess(learn, evolve)
        self.assertLess(evolve, planner)

    def test_style_evolution_does_not_replace_dispatcher_or_operational_crons(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        for cron in (
            '"7,37 * * * *"',
            '"17 0 * * *"',
            '"15 1 * * *"',
            '"17 3 * * *"',
            '"30 14 * * *"',
            '"47 14 * * *"',
            '"57 14 * * 0"',
        ):
            self.assertIn(cron, text)


if __name__ == "__main__":
    unittest.main()
