import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import hardening_runner


class AdaptiveStaleToleranceTests(unittest.TestCase):
    def test_stale_adaptive_maintenance_jobs_still_execute(self):
        scheduled_for = datetime(2026, 8, 31, 0, 27, tzinfo=timezone.utc)
        meta = SimpleNamespace(scheduled_for=scheduled_for, delay_minutes=329, stale=True)

        for action in ("learn", "strategy_guard", "style_evolve", "planner"):
            with self.subTest(action=action):
                job = Mock(return_value=None)
                records = []
                with patch.object(hardening_runner.db, "ensure_schema"), patch.object(
                    hardening_runner.db,
                    "record_job",
                    side_effect=lambda *args: records.append(args),
                ), patch.object(
                    hardening_runner.scheduler,
                    "schedule_metadata",
                    return_value=meta,
                ), patch.object(
                    hardening_runner.notifications,
                    "send_stale",
                ) as send_stale, patch.object(
                    hardening_runner,
                    "utc_now_iso",
                    side_effect=["start", "finish"],
                ), patch.dict(
                    os.environ,
                    {
                        "GITHUB_RUN_ID": f"bootstrap-{action}",
                        "GITHUB_RUN_ATTEMPT": "1",
                        "SCHEDULED_CRON": "27 0 * * *",
                    },
                    clear=False,
                ):
                    outcome = hardening_runner.run_action(action, jobs={action: job})

                self.assertEqual(outcome.status, "success")
                job.assert_called_once_with()
                send_stale.assert_not_called()
                self.assertEqual(records[-1][2], "success")
                self.assertEqual(records[-1][-2:], (scheduled_for.isoformat(), 329))


if __name__ == "__main__":
    unittest.main()
