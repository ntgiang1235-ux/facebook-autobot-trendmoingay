import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import app.db as db


class DailyPlanSchemaTests(unittest.TestCase):
    def test_ensure_schema_creates_daily_plan_additively_and_idempotently(self):
        calls = []

        def fake_execute(query, params=()):
            calls.append((query, params))
            if "PRAGMA table_info(job_runs)" in query:
                return [
                    (0, "run_key", "TEXT", 0, None, 1),
                    (1, "action", "TEXT", 1, None, 0),
                    (2, "status", "TEXT", 1, None, 0),
                    (3, "scheduled_for", "TEXT", 0, None, 0),
                    (4, "delay_minutes", "INTEGER", 0, None, 0),
                ]
            return []

        with patch.object(db, "execute", side_effect=fake_execute):
            db.ensure_schema()
            db.ensure_schema()

        queries = [query for query, _ in calls]
        schema = "\n".join(
            query for query in queries if "CREATE TABLE IF NOT EXISTS daily_plan" in query
        )
        self.assertTrue(schema)
        for field in (
            "plan_date", "slot_id", "planned_for", "action", "category",
            "strategy_mode", "strategy_version", "status", "claim_run_key",
            "claimed_at", "finished_at", "detail", "created_at",
        ):
            self.assertIn(field, schema)
        self.assertIn("UNIQUE(plan_date, slot_id)", schema)
        self.assertTrue(any("idx_daily_plan_due" in query for query in queries))
        self.assertFalse(any("DROP TABLE" in query.upper() for query in queries))


class DailyPlanRepositoryTests(unittest.TestCase):
    def test_save_slots_is_idempotent_per_plan_date_and_slot_id(self):
        from app.plan_repository import DailyPlanSlot, save_slots

        execute = Mock(return_value=[])
        slot = DailyPlanSlot(
            plan_date="2026-08-30",
            slot_id="0830-post-01",
            planned_for="2026-08-30T01:30:00+00:00",
            action="post",
            category="news",
            strategy_mode="baseline",
            strategy_version=7,
            status="planned",
            claim_run_key=None,
            claimed_at=None,
            finished_at=None,
            detail="",
            created_at="2026-08-30T00:47:00+00:00",
        )

        save_slots(execute, [slot])

        query, params = execute.call_args.args
        self.assertIn("INSERT OR IGNORE INTO daily_plan", query)
        self.assertIn("plan_date", query)
        self.assertIn("slot_id", query)
        self.assertEqual(params[0:2], ("2026-08-30", "0830-post-01"))

    def test_list_slots_maps_typed_rows_in_planned_order(self):
        from app.plan_repository import list_slots

        execute = Mock(return_value=[(
            "2026-08-30", "0830-post-01", "2026-08-30T01:30:00+00:00",
            "post", "news", "exploit", 9, "planned", None, None, None, "", "created",
        )])

        slots = list_slots(execute, "2026-08-30")

        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0].action, "post")
        self.assertEqual(slots[0].strategy_version, 9)
        query, params = execute.call_args.args
        self.assertIn("ORDER BY planned_for, slot_id", query)
        self.assertEqual(params, ("2026-08-30",))

    def test_claim_due_slot_expires_too_old_then_atomically_claims_one(self):
        from app.plan_repository import claim_due_slot

        execute = Mock(side_effect=[[], [(
            "2026-08-30", "0830-post-01", "2026-08-30T01:30:00+00:00",
            "post", "news", "exploit", 9, "claimed", "run-1",
            "2026-08-30T01:37:00+00:00", None, "", "created",
        )]])
        now = datetime(2026, 8, 30, 1, 37, tzinfo=timezone.utc)

        slot = claim_due_slot(
            execute,
            plan_date="2026-08-30",
            now=now,
            run_key="run-1",
            grace_minutes=20,
        )

        self.assertIsNotNone(slot)
        self.assertEqual(slot.slot_id, "0830-post-01")
        self.assertEqual(slot.status, "claimed")
        self.assertEqual(execute.call_count, 2)
        expire_query, expire_params = execute.call_args_list[0].args
        claim_query, claim_params = execute.call_args_list[1].args
        self.assertIn("status = 'expired'", expire_query)
        self.assertIn("status = 'planned'", expire_query)
        self.assertIn("UPDATE daily_plan", claim_query)
        self.assertIn("RETURNING", claim_query)
        self.assertIn("ORDER BY planned_for, slot_id", claim_query)
        self.assertIn("status = 'planned'", claim_query)
        self.assertIn("run-1", claim_params)
        self.assertEqual(expire_params[0], "2026-08-30")

    def test_finish_slot_requires_matching_claim_owner(self):
        from app.plan_repository import finish_slot

        execute = Mock(return_value=[])
        finish_slot(
            execute,
            plan_date="2026-08-30",
            slot_id="0830-post-01",
            run_key="run-1",
            status="published",
            finished_at="2026-08-30T01:38:00+00:00",
            detail="ok",
        )

        query, params = execute.call_args.args
        self.assertIn("claim_run_key = ?", query)
        self.assertIn("status = 'claimed'", query)
        self.assertEqual(params[-3:], ("2026-08-30", "0830-post-01", "run-1"))


if __name__ == "__main__":
    unittest.main()
