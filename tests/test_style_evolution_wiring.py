import unittest
from pathlib import Path
from unittest.mock import patch

import hardening_runner
from app.style_evolution import EvolutionResult
from app.style_experiment_lifecycle import LifecycleResult


WORKFLOW = Path(".github/workflows/facebook-autobot.yml")


class StyleEvolutionWiringTests(unittest.TestCase):
    def test_hardening_runner_exposes_style_evolve_action_and_routes_shared_dependencies(self):
        lifecycle = LifecycleResult(status="no_pending", detail="no pending style experiment")
        result = EvolutionResult(
            status="created",
            dimension="tone",
            value="witty_short_punchline",
            parent_value="witty",
            style_id=42,
            detail="controlled style experiment created",
        )
        with patch(
            "hardening_runner.style_experiment_lifecycle.review_pending_experiment",
            return_value=lifecycle,
        ) as review, patch(
            "hardening_runner.style_evolution.generate_next_experiment",
            return_value=result,
        ) as evolve:
            jobs = hardening_runner.resolve_jobs()
            self.assertIn("style_evolve", hardening_runner.VALID_ACTIONS)
            outcome = jobs["style_evolve"]()

        self.assertEqual(outcome.status, "success")
        self.assertEqual(outcome.detail, "controlled style experiment created")
        review.assert_called_once_with(hardening_runner.db.execute)
        evolve.assert_called_once_with(
            hardening_runner.db.execute,
            hardening_runner.autobot.call_gemini,
        )

    def test_pending_inconclusive_lifecycle_blocks_replacement_generation(self):
        for status in (
            "insufficient_experiment_data",
            "insufficient_parent_data",
            "kept_explore",
        ):
            with self.subTest(status=status):
                lifecycle = LifecycleResult(
                    status=status,
                    value="witty_short_punchline",
                    detail=f"{status}: keep current experiment",
                )
                with patch(
                    "hardening_runner.style_experiment_lifecycle.review_pending_experiment",
                    return_value=lifecycle,
                ), patch(
                    "hardening_runner.style_evolution.generate_next_experiment"
                ) as evolve:
                    outcome = hardening_runner.resolve_jobs()["style_evolve"]()

                self.assertEqual(outcome.status, "skipped")
                self.assertEqual(outcome.detail, f"{status}: keep current experiment")
                evolve.assert_not_called()

    def test_decisive_lifecycle_transition_can_create_one_replacement(self):
        for lifecycle_status in ("promoted", "retired"):
            with self.subTest(lifecycle_status=lifecycle_status):
                lifecycle = LifecycleResult(
                    status=lifecycle_status,
                    value="witty_short_punchline",
                    detail=f"experiment {lifecycle_status}",
                )
                generated = EvolutionResult(
                    status="created",
                    dimension="hook",
                    value="question_with_tension",
                    parent_value="question",
                    style_id=43,
                    detail="controlled style experiment created",
                )
                with patch(
                    "hardening_runner.style_experiment_lifecycle.review_pending_experiment",
                    return_value=lifecycle,
                ), patch(
                    "hardening_runner.style_evolution.generate_next_experiment",
                    return_value=generated,
                ) as evolve:
                    outcome = hardening_runner.resolve_jobs()["style_evolve"]()

                self.assertEqual(outcome.status, "success")
                self.assertIn(lifecycle_status, outcome.detail)
                self.assertIn("controlled style experiment created", outcome.detail)
                evolve.assert_called_once_with(
                    hardening_runner.db.execute,
                    hardening_runner.autobot.call_gemini,
                )

    def test_decisive_lifecycle_transition_is_success_even_without_replacement(self):
        lifecycle = LifecycleResult(
            status="promoted",
            value="witty_short_punchline",
            detail="experiment promoted",
        )
        generated = EvolutionResult(
            status="insufficient_data",
            detail="no mature parent available for next experiment",
        )
        with patch(
            "hardening_runner.style_experiment_lifecycle.review_pending_experiment",
            return_value=lifecycle,
        ), patch(
            "hardening_runner.style_evolution.generate_next_experiment",
            return_value=generated,
        ):
            outcome = hardening_runner.resolve_jobs()["style_evolve"]()

        self.assertEqual(outcome.status, "success")
        self.assertIn("promoted", outcome.detail)
        self.assertIn("no mature parent", outcome.detail)

    def test_lifecycle_failure_propagates_without_generation(self):
        with patch(
            "hardening_runner.style_experiment_lifecycle.review_pending_experiment",
            side_effect=RuntimeError("lifecycle database unavailable"),
        ), patch(
            "hardening_runner.style_evolution.generate_next_experiment"
        ) as evolve:
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                hardening_runner.resolve_jobs()["style_evolve"]()

        evolve.assert_not_called()

    def test_hardened_style_evolution_fails_on_dependency_or_registry_failure(self):
        lifecycle = LifecycleResult(status="no_pending", detail="no pending style experiment")
        for status in ("generation_unavailable", "registry_write_failed"):
            with self.subTest(status=status):
                result = EvolutionResult(status=status, detail=f"{status}: unavailable")
                with patch(
                    "hardening_runner.style_experiment_lifecycle.review_pending_experiment",
                    return_value=lifecycle,
                ), patch(
                    "hardening_runner.style_evolution.generate_next_experiment",
                    return_value=result,
                ):
                    jobs = hardening_runner.resolve_jobs()
                    with self.assertRaisesRegex(RuntimeError, "unavailable"):
                        jobs["style_evolve"]()

    def test_hardened_style_evolution_records_normal_noop_as_skipped(self):
        lifecycle = LifecycleResult(status="no_pending", detail="no pending style experiment")
        for status in (
            "pending_existing",
            "insufficient_data",
            "invalid_variant",
            "duplicate_variant",
        ):
            with self.subTest(status=status):
                result = EvolutionResult(status=status, detail=f"{status}: no new experiment")
                with patch(
                    "hardening_runner.style_experiment_lifecycle.review_pending_experiment",
                    return_value=lifecycle,
                ), patch(
                    "hardening_runner.style_evolution.generate_next_experiment",
                    return_value=result,
                ):
                    outcome = hardening_runner.resolve_jobs()["style_evolve"]()

                self.assertEqual(outcome.status, "skipped")
                self.assertEqual(outcome.detail, f"{status}: no new experiment")

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
