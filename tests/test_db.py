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

    def test_ensure_schema_creates_job_runs_table(self):
        with patch.object(db, "execute", return_value=[]) as execute:
            db.ensure_schema()

        query = execute.call_args.args[0]
        self.assertIn("CREATE TABLE IF NOT EXISTS job_runs", query)
        self.assertIn("run_key TEXT PRIMARY KEY", query)
        self.assertIn("status TEXT NOT NULL", query)

    def test_record_job_upserts_run_status(self):
        with patch.object(db, "execute", return_value=[]) as execute:
            db.record_job(
                "run-1",
                "post",
                "failed",
                "2026-08-29T15:00:00+07:00",
                "2026-08-29T15:00:10+07:00",
                "facebook failed",
            )

        query, params = execute.call_args.args
        self.assertIn("INSERT INTO job_runs", query)
        self.assertIn("ON CONFLICT(run_key) DO UPDATE", query)
        self.assertEqual(
            params,
            (
                "run-1",
                "post",
                "failed",
                "2026-08-29T15:00:00+07:00",
                "2026-08-29T15:00:10+07:00",
                "facebook failed",
            ),
        )


if __name__ == "__main__":
    unittest.main()
