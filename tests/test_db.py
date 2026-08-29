import unittest
from unittest.mock import patch

import app.db as db


class FakeCursor:
    def fetchall(self):
        return [("ok",)]


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.committed = False
        self.closed = False

    def execute(self, query, params=()):
        self.calls.append((query, params))
        return FakeCursor()

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


class DatabaseTests(unittest.TestCase):
    def test_execute_uses_retry_and_closes_connection(self):
        connection = FakeConnection()
        with patch.object(db, "TURSO_DATABASE_URL", "libsql://example"), patch.object(
            db, "TURSO_AUTH_TOKEN", "secret"
        ), patch.object(db.libsql, "connect", return_value=connection) as connect, patch.object(
            db, "run_with_retry", side_effect=lambda operation: operation()
        ) as retry:
            rows = db.execute("SELECT 1", ("x",))

        self.assertEqual(rows, [("ok",)])
        retry.assert_called_once()
        connect.assert_called_once_with(database="libsql://example", auth_token="secret")
        self.assertEqual(connection.calls, [("SELECT 1", ("x",))])
        self.assertTrue(connection.committed)
        self.assertTrue(connection.closed)

    def test_ensure_schema_adds_schedule_metadata_columns(self):
        calls = []

        def fake_execute(query, params=()):
            calls.append((query, params))
            if "PRAGMA table_info(job_runs)" in query:
                return [
                    (0, "run_key", "TEXT", 0, None, 1),
                    (1, "action", "TEXT", 1, None, 0),
                    (2, "status", "TEXT", 1, None, 0),
                ]
            return []

        with patch.object(db, "execute", side_effect=fake_execute):
            db.ensure_schema()

        queries = [query for query, _ in calls]
        self.assertIn("scheduled_for TEXT", queries[0])
        self.assertIn("delay_minutes INTEGER", queries[0])
        self.assertTrue(any("ADD COLUMN scheduled_for TEXT" in query for query in queries))
        self.assertTrue(any("ADD COLUMN delay_minutes INTEGER" in query for query in queries))

    def test_record_job_upserts_run_status_with_schedule_metadata(self):
        with patch.object(db, "execute", return_value=[]) as execute:
            db.record_job(
                "run-1",
                "post",
                "failed",
                "2026-08-29T08:31:00+00:00",
                "2026-08-29T08:31:10+00:00",
                "facebook failed",
                "2026-08-29T08:30:00+00:00",
                1,
            )

        query, params = execute.call_args.args
        self.assertIn("scheduled_for", query)
        self.assertIn("delay_minutes", query)
        self.assertIn("ON CONFLICT(run_key) DO UPDATE", query)
        self.assertEqual(
            params,
            (
                "run-1",
                "post",
                "failed",
                "2026-08-29T08:31:00+00:00",
                "2026-08-29T08:31:10+00:00",
                "facebook failed",
                "2026-08-29T08:30:00+00:00",
                1,
            ),
        )


if __name__ == "__main__":
    unittest.main()
