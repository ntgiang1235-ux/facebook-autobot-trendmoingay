import unittest
from datetime import datetime, timezone

from app.scheduler import schedule_metadata


class SchedulerTests(unittest.TestCase):
    def test_schedule_metadata_calculates_small_delay(self):
        now = datetime(2026, 8, 29, 9, 12, tzinfo=timezone.utc)
        meta = schedule_metadata("7 9 * * *", now=now, stale_after_minutes=60)

        self.assertEqual(meta.scheduled_for, datetime(2026, 8, 29, 9, 7, tzinfo=timezone.utc))
        self.assertEqual(meta.delay_minutes, 5)
        self.assertFalse(meta.stale)

    def test_schedule_metadata_marks_large_delay_stale(self):
        now = datetime(2026, 8, 29, 14, 11, tzinfo=timezone.utc)
        meta = schedule_metadata("7 9 * * *", now=now, stale_after_minutes=60)

        self.assertEqual(meta.delay_minutes, 304)
        self.assertTrue(meta.stale)

    def test_manual_run_has_no_schedule_metadata(self):
        now = datetime(2026, 8, 29, 14, 11, tzinfo=timezone.utc)
        meta = schedule_metadata("", now=now)

        self.assertIsNone(meta.scheduled_for)
        self.assertIsNone(meta.delay_minutes)
        self.assertFalse(meta.stale)


if __name__ == "__main__":
    unittest.main()
