import unittest

from db_retry import is_transient_turso_error, run_with_retry


class TursoRetryTests(unittest.TestCase):
    def test_502_upstream_error_is_retryable(self):
        exc = ValueError('Hrana: api error: status=502 Bad Gateway, body={"error":"connect to upstream failed"}')
        self.assertTrue(is_transient_turso_error(exc))

    def test_sql_error_is_not_retryable(self):
        self.assertFalse(is_transient_turso_error(ValueError("SQL error: no such table: missing")))

    def test_transient_error_retries_then_succeeds(self):
        calls = []
        sleeps = []

        def operation():
            calls.append(1)
            if len(calls) < 3:
                raise ValueError("status=502 Bad Gateway connect to upstream failed")
            return "ok"

        result = run_with_retry(operation, sleep_fn=sleeps.append)

        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [2, 5])


if __name__ == "__main__":
    unittest.main()
