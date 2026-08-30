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

    def test_recurring_dispatcher_cron_uses_latest_minute_in_current_hour(self):
        now = datetime(2026, 8, 31, 8, 46, tzinfo=timezone.utc)
        meta = schedule_metadata("7,37 * * * *", now=now, stale_after_minutes=60)

        self.assertEqual(meta.scheduled_for, datetime(2026, 8, 31, 8, 37, tzinfo=timezone.utc))
        self.assertEqual(meta.delay_minutes, 9)
        self.assertFalse(meta.stale)

    def test_recurring_dispatcher_cron_rolls_back_to_previous_hour(self):
        now = datetime(2026, 8, 31, 8, 5, tzinfo=timezone.utc)
        meta = schedule_metadata("7,37 * * * *", now=now, stale_after_minutes=60)

        self.assertEqual(meta.scheduled_for, datetime(2026, 8, 31, 7, 37, tzinfo=timezone.utc))
        self.assertEqual(meta.delay_minutes, 28)
        self.assertFalse(meta.stale)

    def test_recurring_dispatcher_cron_can_be_marked_stale(self):
        now = datetime(2026, 8, 31, 8, 59, tzinfo=timezone.utc)
        meta = schedule_metadata("7,37 * * * *", now=now, stale_after_minutes=15)

        self.assertEqual(meta.scheduled_for, datetime(2026, 8, 31, 8, 37, tzinfo=timezone.utc))
        self.assertEqual(meta.delay_minutes, 22)
        self.assertTrue(meta.stale)

    def test_weekly_cron_uses_current_sunday_occurrence(self):
        now = datetime(2026, 8, 30, 15, 2, tzinfo=timezone.utc)
        meta = schedule_metadata("57 14 * * 0", now=now, stale_after_minutes=60)

        self.assertEqual(meta.scheduled_for, datetime(2026, 8, 30, 14, 57, tzinfo=timezone.utc))
        self.assertEqual(meta.delay_minutes, 5)
        self.assertFalse(meta.stale)

    def test_weekly_cron_rolls_back_to_previous_sunday(self):
        now = datetime(2026, 8, 31, 1, 0, tzinfo=timezone.utc)
        meta = schedule_metadata("57 14 * * 0", now=now, stale_after_minutes=1000)

        self.assertEqual(meta.scheduled_for, datetime(2026, 8, 30, 14, 57, tzinfo=timezone.utc))
        self.assertEqual(meta.delay_minutes, 603)
        self.assertFalse(meta.stale)

    def test_manual_run_has_no_schedule_metadata(self):
        now = datetime(2026, 8, 29, 14, 11, tzinfo=timezone.utc)
        meta = schedule_metadata("", now=now)

        self.assertIsNone(meta.scheduled_for)
        self.assertIsNone(meta.delay_minutes)
        self.assertFalse(meta.stale)

    def test_unsupported_complex_cron_fails_closed(self):
        now = datetime(2026, 8, 31, 8, 5, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "không hỗ trợ"):
            schedule_metadata("*/10 8-20 * * 1-5", now=now)


if __name__ == "__main__":
    unittest.main()
