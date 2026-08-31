import unittest
from unittest.mock import patch

import app.db as db


class FakeCursor:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    def __init__(self, *, fail_on=None):
        self.fail_on = fail_on
        self.calls = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, query, params=()):
        self.calls.append((query, params))
        if self.fail_on and self.fail_on in query:
            raise RuntimeError("statement failed")
        return FakeCursor([(query,)])

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class DatabaseTransactionTests(unittest.TestCase):
    def test_execute_transaction_uses_one_connection_and_one_commit(self):
        connection = FakeConnection()

        def operation(execute):
            first = execute("UPDATE first SET value = ?", (1,))
            second = execute("UPDATE second SET value = ?", (2,))
            return first, second

        with patch.object(db, "TURSO_DATABASE_URL", "libsql://example"), patch.object(
            db, "TURSO_AUTH_TOKEN", "secret"
        ), patch.object(db.libsql, "connect", return_value=connection) as connect, patch.object(
            db, "run_with_retry", side_effect=lambda callback: callback()
        ) as retry:
            result = db.execute_transaction(operation)

        self.assertEqual(len(connection.calls), 2)
        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)
        self.assertTrue(connection.closed)
        retry.assert_called_once()
        connect.assert_called_once_with(database="libsql://example", auth_token="secret")
        self.assertEqual(result[0], [("UPDATE first SET value = ?",)])
        self.assertEqual(result[1], [("UPDATE second SET value = ?",)])

    def test_execute_transaction_rolls_back_entire_connection_on_statement_failure(self):
        connection = FakeConnection(fail_on="UPDATE second")

        def operation(execute):
            execute("UPDATE first SET value = ?", (1,))
            execute("UPDATE second SET value = ?", (2,))

        with patch.object(db, "TURSO_DATABASE_URL", "libsql://example"), patch.object(
            db, "TURSO_AUTH_TOKEN", "secret"
        ), patch.object(db.libsql, "connect", return_value=connection), patch.object(
            db, "run_with_retry", side_effect=lambda callback: callback()
        ):
            with self.assertRaisesRegex(RuntimeError, "statement failed"):
                db.execute_transaction(operation)

        self.assertFalse(connection.committed)
        self.assertTrue(connection.rolled_back)
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
