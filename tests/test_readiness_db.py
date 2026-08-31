import unittest
from unittest import mock

from app import readiness_db


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return list(self.rows)


class _FakeConnection:
    def __init__(self):
        self.rows = [(1,)]
        self.execute_error = None
        self.executed = []
        self.commit_calls = 0
        self.close_calls = 0

    def execute(self, query, params=()):
        self.executed.append((query, params))
        if self.execute_error is not None:
            raise self.execute_error
        return _FakeCursor(self.rows)

    def commit(self):
        self.commit_calls += 1

    def close(self):
        self.close_calls += 1


class ReadinessDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.connection = _FakeConnection()
        self.connect_calls = []

        def fake_connect(**kwargs):
            self.connect_calls.append(kwargs)
            return self.connection

        self.patches = (
            mock.patch.object(readiness_db, "TURSO_DATABASE_URL", "libsql://readiness-test"),
            mock.patch.object(readiness_db, "TURSO_AUTH_TOKEN", "token"),
            mock.patch.object(readiness_db.libsql, "connect", side_effect=fake_connect),
        )
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()

    def test_select_and_table_info_are_allowed(self):
        self.assertEqual(readiness_db.validate_read_query(" SELECT 1"), "SELECT")
        self.assertEqual(
            readiness_db.validate_read_query("PRAGMA table_info(adaptive_config)"),
            "PRAGMA",
        )
        self.assertEqual(
            readiness_db.validate_read_query("-- comment\nSELECT 1"),
            "SELECT",
        )
        self.assertEqual(
            readiness_db.validate_read_query("/* comment */ PRAGMA table_info(strategy_stats)"),
            "PRAGMA",
        )

    def test_mutating_statements_are_rejected_before_connect(self):
        for query in (
            "UPDATE adaptive_config SET adaptive_enabled = 0",
            "INSERT INTO adaptive_config(id) VALUES (1)",
            "DELETE FROM strategy_stats",
            "CREATE TABLE x(id INTEGER)",
            "ALTER TABLE strategy_stats ADD COLUMN x INTEGER",
            "DROP TABLE strategy_stats",
            "REPLACE INTO adaptive_config(id) VALUES (1)",
            "WITH x AS (SELECT 1) SELECT * FROM x",
        ):
            with self.subTest(query=query):
                with self.assertRaises(ValueError):
                    readiness_db.execute_read(query)
        self.assertEqual(self.connect_calls, [])

    def test_malformed_pragma_is_rejected_before_connect(self):
        for query in (
            "PRAGMA writable_schema = 1",
            "PRAGMA journal_mode=WAL",
            "PRAGMA table_info(adaptive_config); SELECT 1",
        ):
            with self.subTest(query=query):
                with self.assertRaises(ValueError):
                    readiness_db.execute_read(query)
        self.assertEqual(self.connect_calls, [])

    def test_execute_read_never_commits_and_always_closes(self):
        rows = readiness_db.execute_read("SELECT 1")
        self.assertEqual(rows, [(1,)])
        self.assertEqual(self.connection.commit_calls, 0)
        self.assertEqual(self.connection.close_calls, 1)
        self.assertEqual(len(self.connect_calls), 1)

    def test_connection_closes_when_execute_raises(self):
        self.connection.execute_error = RuntimeError("query failed")
        with self.assertRaisesRegex(RuntimeError, "query failed"):
            readiness_db.execute_read("SELECT 1")
        self.assertEqual(self.connection.commit_calls, 0)
        self.assertEqual(self.connection.close_calls, 1)

    def test_transient_transport_path_uses_existing_retry_wrapper(self):
        with mock.patch.object(
            readiness_db,
            "run_with_retry",
            side_effect=lambda operation: operation(),
        ) as retry:
            self.assertEqual(readiness_db.execute_read("SELECT 1"), [(1,)])
        retry.assert_called_once()

    def test_missing_config_fails_before_connect(self):
        with mock.patch.object(readiness_db, "TURSO_DATABASE_URL", ""):
            with self.assertRaisesRegex(RuntimeError, "TURSO_DATABASE_URL"):
                readiness_db.execute_read("SELECT 1")
        self.assertEqual(self.connect_calls, [])


if __name__ == "__main__":
    unittest.main()
