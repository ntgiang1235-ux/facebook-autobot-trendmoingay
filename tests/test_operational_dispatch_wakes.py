import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import hardening_runner
from app.job_contract import skipped, success


def jobs_with_primary(action: str, primary_job):
    jobs = {
        name: Mock(return_value=success(f"{name} ok"))
        for name in hardening_runner.ADAPTIVE_CONTENT_ACTIONS
    }
    jobs[action] = primary_job
    return jobs


class OperationalDispatchWakeTests(unittest.TestCase):
    def setUp(self):
        self.scheduled_for = datetime(2026, 8, 31, 9, 17, tzinfo=timezone.utc)

    def test_scheduled_non_dispatch_action_attempts_one_distinct_dispatch(self):
        primary = Mock(return_value=success("health ok"))
        jobs = jobs_with_primary("health", primary)
        records = []
        meta = SimpleNamespace(
            scheduled_for=self.scheduled_for,
            delay_minutes=8,
            stale=False,
        )

        with patch.object(hardening_runner.db, "ensure_schema"), patch.object(
            hardening_runner.db,
            "record_job",
            side_effect=lambda *args: records.append(args),
        ), patch.object(
            hardening_runner.scheduler,
            "schedule_metadata",
            return_value=meta,
        ), patch.object(
            hardening_runner.dispatcher,
            "dispatch_due",
            return_value=skipped("no due plan slot"),
        ) as dispatch, patch.object(
            hardening_runner,
            "utc_now_iso",
            side_effect=["primary-start", "dispatch-start", "dispatch-finish", "primary-finish"],
        ), patch.dict(
            os.environ,
            {
                "GITHUB_EVENT_NAME": "schedule",
                "GITHUB_RUN_ID": "wake-1",
                "GITHUB_RUN_ATTEMPT": "1",
                "SCHEDULED_CRON": "17 9 * * *",
            },
            clear=False,
        ):
            outcome = hardening_runner.run_action("health", jobs=jobs)

        self.assertEqual(outcome.status, "success")
        primary.assert_called_once_with()
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.kwargs["run_key"], "wake-1-1-health-opportunistic-dispatch")
        dispatch_actions = [record for record in records if record[1] == "dispatch"]
        self.assertEqual(len(dispatch_actions), 2)
        self.assertEqual(dispatch_actions[0][0], "wake-1-1-health-opportunistic-dispatch")
        self.assertEqual(dispatch_actions[0][2], "started")
        self.assertEqual(dispatch_actions[1][2], "skipped")

    def test_manual_non_dispatch_action_never_publishes_opportunistically(self):
        primary = Mock(return_value=success("health ok"))
        jobs = jobs_with_primary("health", primary)
        meta = SimpleNamespace(scheduled_for=None, delay_minutes=None, stale=False)

        with patch.object(hardening_runner.db, "ensure_schema"), patch.object(
            hardening_runner.db, "record_job"
        ), patch.object(
            hardening_runner.scheduler, "schedule_metadata", return_value=meta
        ), patch.object(
            hardening_runner.dispatcher, "dispatch_due"
        ) as dispatch, patch.object(
            hardening_runner, "utc_now_iso", side_effect=["start", "finish"]
        ), patch.dict(
            os.environ,
            {
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_RUN_ID": "manual-1",
                "GITHUB_RUN_ATTEMPT": "1",
                "SCHEDULED_CRON": "",
            },
            clear=False,
        ):
            outcome = hardening_runner.run_action("health", jobs=jobs)

        self.assertEqual(outcome.status, "success")
        primary.assert_called_once_with()
        dispatch.assert_not_called()

    def test_explicit_dispatch_schedule_is_not_double_dispatched(self):
        explicit = Mock(return_value=skipped("no due plan slot"))
        meta = SimpleNamespace(
            scheduled_for=self.scheduled_for,
            delay_minutes=7,
            stale=False,
        )

        with patch.object(hardening_runner.db, "ensure_schema"), patch.object(
            hardening_runner.db, "record_job"
        ), patch.object(
            hardening_runner.scheduler, "schedule_metadata", return_value=meta
        ), patch.object(
            hardening_runner.dispatcher, "dispatch_due"
        ) as implicit, patch.object(
            hardening_runner, "utc_now_iso", side_effect=["start", "finish"]
        ), patch.dict(
            os.environ,
            {
                "GITHUB_EVENT_NAME": "schedule",
                "GITHUB_RUN_ID": "dispatch-1",
                "GITHUB_RUN_ATTEMPT": "1",
                "SCHEDULED_CRON": "7,37 * * * *",
            },
            clear=False,
        ):
            outcome = hardening_runner.run_action("dispatch", jobs={"dispatch": explicit})

        self.assertEqual(outcome.status, "skipped")
        explicit.assert_called_once_with()
        implicit.assert_not_called()

    def test_stale_scheduled_primary_can_dispatch_current_slot_before_skip(self):
        recipe = Mock(return_value=success("recipe should not run"))
        jobs = jobs_with_primary("recipe", recipe)
        records = []
        meta = SimpleNamespace(
            scheduled_for=self.scheduled_for,
            delay_minutes=304,
            stale=True,
        )

        with patch.object(hardening_runner.db, "ensure_schema"), patch.object(
            hardening_runner.db,
            "record_job",
            side_effect=lambda *args: records.append(args),
        ), patch.object(
            hardening_runner.scheduler, "schedule_metadata", return_value=meta
        ), patch.object(
            hardening_runner.dispatcher,
            "dispatch_due",
            return_value=skipped("no due plan slot"),
        ) as dispatch, patch.object(
            hardening_runner.notifications, "send_stale"
        ) as send_stale, patch.object(
            hardening_runner,
            "utc_now_iso",
            side_effect=["primary-start", "dispatch-start", "dispatch-finish", "primary-finish"],
        ), patch.dict(
            os.environ,
            {
                "GITHUB_EVENT_NAME": "schedule",
                "GITHUB_RUN_ID": "stale-1",
                "GITHUB_RUN_ATTEMPT": "1",
                "SCHEDULED_CRON": "17 9 * * *",
            },
            clear=False,
        ):
            outcome = hardening_runner.run_action("recipe", jobs=jobs)

        self.assertEqual(outcome.status, "skipped")
        self.assertIn("304", outcome.detail)
        dispatch.assert_called_once()
        recipe.assert_not_called()
        send_stale.assert_called_once()
        self.assertEqual([record[1] for record in records[:3]], ["recipe", "dispatch", "dispatch"])


if __name__ == "__main__":
    unittest.main()
