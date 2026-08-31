from pathlib import Path
import unittest
from unittest.mock import patch

import hardening_runner
from app.strategy_guard import StrategyEvidence, StrategyGuardResult


ROOT = Path(__file__).resolve().parents[1]


def evidence(score=80.0, count=8, coverage=1.0):
    return StrategyEvidence(
        start_at="2026-08-15T00:00:00+00:00",
        end_at="2026-08-22T00:00:00+00:00",
        eligible_count=count,
        final_count=count,
        average_score=score,
        coverage=coverage,
    )


def result(status, *, detail=None):
    return StrategyGuardResult(
        status=status,
        recent=evidence(70.0),
        prior=evidence(90.0),
        regression_ratio=0.2222,
        current_version=9,
        last_good_version=7,
        rollback_version=9 if status == "rolled_back" else None,
        detail=detail or status,
    )


class StrategyGuardWiringTests(unittest.TestCase):
    def test_hardening_runner_routes_strategy_guard_through_shared_db(self):
        with patch.object(
            hardening_runner.strategy_guard,
            "run_strategy_guard",
            return_value=result("stable", detail="healthy"),
        ) as guard:
            jobs = hardening_runner.resolve_jobs()
            outcome = jobs["strategy_guard"]()

        self.assertEqual(outcome.status, "success")
        self.assertEqual(outcome.detail, "healthy")
        guard.assert_called_once_with(
            hardening_runner.db.execute,
            transaction_fn=hardening_runner.db.execute_transaction,
        )

    def test_normal_guard_noop_states_are_skipped(self):
        for status in (
            "disabled",
            "no_current",
            "insufficient_data",
            "metric_degraded",
            "no_last_good",
        ):
            with self.subTest(status=status), patch.object(
                hardening_runner.strategy_guard,
                "run_strategy_guard",
                return_value=result(status),
            ):
                outcome = hardening_runner.resolve_jobs()["strategy_guard"]()
            self.assertEqual(outcome.status, "skipped")
            self.assertEqual(outcome.detail, status)

    def test_stable_and_last_good_promotion_are_success(self):
        for status in ("stable", "promoted_last_good"):
            with self.subTest(status=status), patch.object(
                hardening_runner.strategy_guard,
                "run_strategy_guard",
                return_value=result(status),
            ):
                outcome = hardening_runner.resolve_jobs()["strategy_guard"]()
            self.assertEqual(outcome.status, "success")

    def test_rollback_succeeds_and_sends_best_effort_telegram_alert(self):
        rollback = result("rolled_back", detail="automatic rollback to v7")
        with patch.object(
            hardening_runner.strategy_guard,
            "run_strategy_guard",
            return_value=rollback,
        ), patch.object(
            hardening_runner.notifications,
            "send_message",
            return_value=False,
        ) as send:
            outcome = hardening_runner.resolve_jobs()["strategy_guard"]()

        self.assertEqual(outcome.status, "success")
        self.assertEqual(outcome.detail, "automatic rollback to v7")
        send.assert_called_once()
        alert = send.call_args.args[0]
        self.assertIn("STRATEGY ROLLBACK", alert)
        self.assertIn("v7", alert)
        self.assertIn("22.2%", alert)

    def test_unknown_strategy_guard_status_fails_closed(self):
        with patch.object(
            hardening_runner.strategy_guard,
            "run_strategy_guard",
            return_value=result("mystery"),
        ):
            with self.assertRaisesRegex(RuntimeError, "strategy guard failed"):
                hardening_runner.resolve_jobs()["strategy_guard"]()

    def test_strategy_guard_dependency_failure_propagates(self):
        with patch.object(
            hardening_runner.strategy_guard,
            "run_strategy_guard",
            side_effect=RuntimeError("turso unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "turso unavailable"):
                hardening_runner.resolve_jobs()["strategy_guard"]()

    def test_production_workflow_runs_strategy_guard_between_learning_and_planner(self):
        prod = (ROOT / ".github/workflows/facebook-autobot.yml").read_text(encoding="utf-8")

        self.assertIn("          - strategy_guard", prod)
        self.assertIn('cron: "32 0 * * *"', prod)
        self.assertIn('"32 0 * * *") ACTION="strategy_guard"', prod)

        learn = prod.index('cron: "27 0 * * *"')
        guard = prod.index('cron: "32 0 * * *"')
        style = prod.index('cron: "37 0 * * 0"')
        planner = prod.index('cron: "47 0 * * *"')
        self.assertLess(learn, guard)
        self.assertLess(guard, style)
        self.assertLess(style, planner)


if __name__ == "__main__":
    unittest.main()
