import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.job_contract import skipped, success
from app.plan_repository import DailyPlanSlot


def claimed_slot(action: str = "post") -> DailyPlanSlot:
    return DailyPlanSlot(
        plan_date="2026-08-31",
        slot_id="0830-post-01",
        planned_for="2026-08-31T01:30:00+00:00",
        action=action,
        category=action,
        strategy_mode="exploit",
        strategy_version=12,
        status="claimed",
        claim_run_key="run-1",
        claimed_at="2026-08-31T01:37:00+00:00",
        finished_at=None,
        detail="",
        created_at="2026-08-31T00:47:00+00:00",
    )


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 31, 1, 37, tzinfo=timezone.utc)

    def test_plan_date_uses_vietnam_local_day_not_utc_day(self):
        from app.dispatcher import local_plan_date

        late_utc = datetime(2026, 8, 30, 18, 10, tzinfo=timezone.utc)
        self.assertEqual(local_plan_date(late_utc), "2026-08-31")

    def test_no_due_slot_is_successful_skip_and_executes_no_business_job(self):
        from app.dispatcher import dispatch_due

        execute = Mock()
        job = Mock()
        with patch("app.dispatcher.claim_due_slot", return_value=None) as claim, patch(
            "app.dispatcher.finish_slot"
        ) as finish:
            outcome = dispatch_due(
                execute,
                {"post": job},
                now=self.now,
                run_key="run-1",
                grace_minutes=20,
            )

        self.assertEqual(outcome.status, "skipped")
        self.assertIn("no due", outcome.detail)
        claim.assert_called_once()
        job.assert_not_called()
        finish.assert_not_called()

    def test_successful_claim_executes_exactly_one_job_and_marks_published(self):
        from app.dispatcher import dispatch_due

        execute = Mock()
        job = Mock(return_value=success("posted"))
        slot = claimed_slot("post")
        with patch("app.dispatcher.claim_due_slot", return_value=slot), patch(
            "app.dispatcher.finish_slot"
        ) as finish, patch(
            "app.dispatcher.select_creative_profile",
            return_value=SimpleNamespace(
                hook_type="question",
                style_type="conversational",
                cta_type="opinion_question",
            ),
        ):
            outcome = dispatch_due(execute, {"post": job}, now=self.now, run_key="run-1")

        self.assertEqual(outcome.status, "success")
        job.assert_called_once_with()
        finish.assert_called_once()
        kwargs = finish.call_args.kwargs
        self.assertEqual(kwargs["status"], "published")
        self.assertEqual(kwargs["slot_id"], slot.slot_id)
        self.assertEqual(kwargs["run_key"], "run-1")

    def test_claimed_slot_context_is_visible_only_while_business_job_runs(self):
        from app.dispatcher import dispatch_due
        from app.publication_context import current_publication_context

        execute = Mock()
        slot = claimed_slot("finance")
        observed = []
        profile = SimpleNamespace(
            hook_type="contrast",
            style_type="explanatory",
            cta_type="choose_side",
        )

        def job():
            observed.append(current_publication_context())
            return success("posted")

        with patch("app.dispatcher.claim_due_slot", return_value=slot), patch(
            "app.dispatcher.finish_slot"
        ), patch(
            "app.dispatcher.select_creative_profile", return_value=profile
        ) as select_profile:
            dispatch_due(execute, {"finance": job}, now=self.now, run_key="run-42")

        self.assertEqual(len(observed), 1)
        context = observed[0]
        self.assertEqual(context.run_key, "run-42")
        self.assertEqual(context.category, "finance")
        self.assertEqual(context.scheduled_for, slot.planned_for)
        self.assertEqual(context.strategy_mode, "exploit")
        self.assertEqual(context.strategy_version, 12)
        self.assertEqual(context.hook_type, "contrast")
        self.assertEqual(context.style_type, "explanatory")
        self.assertEqual(context.cta_type, "choose_side")
        select_profile.assert_called_once_with(
            execute,
            run_key="run-42",
            category="finance",
            strategy_version=12,
        )
        self.assertIsNone(current_publication_context())

    def test_creative_selector_failure_marks_slot_failed_before_business_job(self):
        from app.dispatcher import dispatch_due

        execute = Mock()
        job = Mock()
        slot = claimed_slot("post")
        with patch("app.dispatcher.claim_due_slot", return_value=slot), patch(
            "app.dispatcher.finish_slot"
        ) as finish, patch(
            "app.dispatcher.select_creative_profile",
            side_effect=RuntimeError("creative selector unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "creative selector unavailable"):
                dispatch_due(execute, {"post": job}, now=self.now, run_key="run-1")

        job.assert_not_called()
        self.assertEqual(finish.call_args.kwargs["status"], "failed")
        self.assertIn("creative selector unavailable", finish.call_args.kwargs["detail"])

    def test_business_skip_marks_slot_skipped_not_published(self):
        from app.dispatcher import dispatch_due

        execute = Mock()
        slot = claimed_slot("post")
        with patch("app.dispatcher.claim_due_slot", return_value=slot), patch(
            "app.dispatcher.finish_slot"
        ) as finish, patch(
            "app.dispatcher.select_creative_profile",
            return_value=SimpleNamespace(
                hook_type="question",
                style_type="conversational",
                cta_type="opinion_question",
            ),
        ):
            outcome = dispatch_due(
                execute,
                {"post": lambda: skipped("duplicate")},
                now=self.now,
                run_key="run-1",
            )

        self.assertEqual(outcome.status, "skipped")
        self.assertEqual(finish.call_args.kwargs["status"], "skipped")
        self.assertEqual(finish.call_args.kwargs["detail"], "duplicate")

    def test_business_exception_marks_failed_then_reraises(self):
        from app.dispatcher import dispatch_due

        def fail():
            raise RuntimeError("facebook down")

        execute = Mock()
        slot = claimed_slot("post")
        with patch("app.dispatcher.claim_due_slot", return_value=slot), patch(
            "app.dispatcher.finish_slot"
        ) as finish, patch(
            "app.dispatcher.select_creative_profile",
            return_value=SimpleNamespace(
                hook_type="question",
                style_type="conversational",
                cta_type="opinion_question",
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "facebook down"):
                dispatch_due(execute, {"post": fail}, now=self.now, run_key="run-1")

        self.assertEqual(finish.call_args.kwargs["status"], "failed")
        self.assertIn("facebook down", finish.call_args.kwargs["detail"])

    def test_unknown_claimed_action_is_failed_closed(self):
        from app.dispatcher import dispatch_due

        execute = Mock()
        slot = claimed_slot("unknown")
        with patch("app.dispatcher.claim_due_slot", return_value=slot), patch(
            "app.dispatcher.finish_slot"
        ) as finish:
            with self.assertRaisesRegex(ValueError, "planned action"):
                dispatch_due(execute, {"post": Mock()}, now=self.now, run_key="run-1")

        self.assertEqual(finish.call_args.kwargs["status"], "failed")

    def test_dispatcher_passes_local_plan_date_and_grace_to_atomic_claim(self):
        from app.dispatcher import dispatch_due

        execute = Mock()
        with patch("app.dispatcher.claim_due_slot", return_value=None) as claim:
            dispatch_due(
                execute,
                {},
                now=self.now,
                run_key="run-1",
                grace_minutes=17,
            )

        kwargs = claim.call_args.kwargs
        self.assertEqual(kwargs["plan_date"], "2026-08-31")
        self.assertEqual(kwargs["run_key"], "run-1")
        self.assertEqual(kwargs["grace_minutes"], 17)
        self.assertEqual(kwargs["now"], self.now)


if __name__ == "__main__":
    unittest.main()
