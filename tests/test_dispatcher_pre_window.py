import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from app import dispatcher


class DispatcherPreWindowTests(unittest.TestCase):
    def test_dispatch_before_first_safe_slot_does_not_self_heal_or_claim(self):
        execute = Mock()
        before_window = datetime(2026, 8, 31, 0, 27, tzinfo=timezone.utc)  # 07:27 VN

        with patch.object(dispatcher.adaptive_jobs, "ensure_daily_plan") as ensure, patch.object(
            dispatcher, "claim_due_slot"
        ) as claim:
            outcome = dispatcher.dispatch_due(
                execute,
                {},
                now=before_window,
                run_key="wake-learn-opportunistic-dispatch",
            )

        self.assertEqual(outcome.status, "skipped")
        self.assertIn("before publishing window", outcome.detail)
        ensure.assert_not_called()
        claim.assert_not_called()


if __name__ == "__main__":
    unittest.main()
