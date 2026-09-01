import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

import readiness_runner
from app.readiness import ReadinessCheck, ReadinessResult


class ReadinessRunnerTests(unittest.TestCase):
    def run_main(self, result=None, error=None):
        output = io.StringIO()
        patcher = (
            mock.patch.object(readiness_runner, "run_readiness", side_effect=error)
            if error is not None
            else mock.patch.object(readiness_runner, "run_readiness", return_value=result)
        )
        with patcher, redirect_stdout(output):
            code = readiness_runner.main()
        return code, output.getvalue()

    def test_ready_and_degraded_exit_zero(self):
        for status in ("ready", "degraded"):
            with self.subTest(status=status):
                result = ReadinessResult(
                    status=status,
                    checks=(ReadinessCheck("schema", "ready", "ok"),),
                )
                code, output = self.run_main(result=result)
                self.assertEqual(code, 0)
                self.assertIn(f"PHASE_4_READINESS: {status.upper()}", output)
                self.assertIn("[READY] schema — ok", output)

    def test_runner_applies_bootstrap_policy_to_safe_learning_warmup(self):
        result = ReadinessResult(
            status="degraded",
            checks=(
                ReadinessCheck("schema", "ready", "ok"),
                ReadinessCheck(
                    "strategy_versions",
                    "degraded",
                    "current=v2; no proven last-good rollback target yet",
                ),
                ReadinessCheck(
                    "learning",
                    "degraded",
                    "insufficient mature learning for: category, time_bucket",
                ),
                ReadinessCheck("liveness", "ready", "healthy"),
            ),
        )

        code, output = self.run_main(result=result)

        self.assertEqual(code, 0)
        self.assertIn("PHASE_4_READINESS: READY", output)
        self.assertIn("[READY] learning — bootstrap-safe", output)
        self.assertIn("[READY] strategy_versions — current=v2; bootstrap-safe", output)

    def test_bootstrap_policy_never_masks_failed_check(self):
        result = ReadinessResult(
            status="failed",
            checks=(
                ReadinessCheck("schema", "failed", "missing required table"),
                ReadinessCheck(
                    "strategy_versions",
                    "degraded",
                    "current=v2; no proven last-good rollback target yet",
                ),
                ReadinessCheck(
                    "learning",
                    "degraded",
                    "insufficient mature learning for: category, time_bucket",
                ),
            ),
        )

        code, output = self.run_main(result=result)

        self.assertEqual(code, 1)
        self.assertIn("PHASE_4_READINESS: FAILED", output)
        self.assertIn("[FAILED] schema — missing required table", output)

    def test_failed_exits_one(self):
        result = ReadinessResult(
            status="failed",
            checks=(ReadinessCheck("schema", "failed", "missing"),),
        )
        code, output = self.run_main(result=result)
        self.assertEqual(code, 1)
        self.assertIn("PHASE_4_READINESS: FAILED", output)
        self.assertIn("[FAILED] schema — missing", output)

    def test_query_exception_exits_one(self):
        code, output = self.run_main(error=RuntimeError("Turso unavailable"))
        self.assertEqual(code, 1)
        self.assertIn("PHASE_4_READINESS: FAILED", output)
        self.assertIn("[FAILED] dependency — Turso unavailable", output)

    def test_format_result_prints_every_structured_check(self):
        result = ReadinessResult(
            status="degraded",
            checks=(
                ReadinessCheck("schema", "ready", "8 tables"),
                ReadinessCheck("learning", "degraded", "warming up"),
            ),
        )
        text = readiness_runner.format_result(result)
        self.assertEqual(
            text,
            "\n".join(
                (
                    "PHASE_4_READINESS: DEGRADED",
                    "[READY] schema — 8 tables",
                    "[DEGRADED] learning — warming up",
                )
            ),
        )


if __name__ == "__main__":
    unittest.main()
