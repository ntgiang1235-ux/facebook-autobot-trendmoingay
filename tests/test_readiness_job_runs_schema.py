import unittest

from app import readiness


class ReadinessJobRunsSchemaTests(unittest.TestCase):
    def test_missing_dispatch_started_at_column_fails_schema_check(self):
        def execute(query, params=()):
            normalized = " ".join(query.split())
            if normalized == "SELECT name FROM sqlite_master WHERE type = 'table'":
                return [(name,) for name in readiness.REQUIRED_COLUMNS]
            if normalized.startswith("PRAGMA table_info("):
                table = normalized.removeprefix("PRAGMA table_info(").removesuffix(")")
                columns = set(readiness.REQUIRED_COLUMNS[table])
                if table == "job_runs":
                    columns.discard("started_at")
                return [(index, column) for index, column in enumerate(sorted(columns))]
            raise AssertionError(f"unexpected query: {query}")

        check = readiness._schema_check(execute)

        self.assertEqual(check.status, "failed")
        self.assertIn("job_runs", check.detail)
        self.assertIn("started_at", check.detail)


if __name__ == "__main__":
    unittest.main()
