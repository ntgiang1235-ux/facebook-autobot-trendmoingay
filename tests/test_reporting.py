import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock


class ReportingTests(unittest.TestCase):
    def test_daily_report_is_compact_and_degrades_without_metrics(self):
        from app.reporting import DailyReportData, build_daily_report

        data = DailyReportData(
            planned=12,
            published=9,
            skipped=1,
            expired=2,
            failed=0,
            average_score=None,
            top_category=None,
            bottom_category=None,
            strategy_version=None,
            baseline_daily_volume=12,
            exploration_rate=0.20,
            metric_warning="chưa có final metrics",
        )
        message = build_daily_report(data, "2026-08-30")
        lines = message.splitlines()

        self.assertLessEqual(len(lines), 8)
        self.assertIn("12", message)
        self.assertIn("9", message)
        self.assertIn("chưa đủ dữ liệu", message)
        self.assertIn("20%", message)
        self.assertIn("chưa có final metrics", message)

    def test_daily_report_escapes_dynamic_category_text(self):
        from app.reporting import DailyReportData, build_daily_report

        data = DailyReportData(
            planned=10,
            published=10,
            skipped=0,
            expired=0,
            failed=0,
            average_score=66.5,
            top_category=("finance <hot>", 82.2),
            bottom_category=("recipe & food", 44.1),
            strategy_version=7,
            baseline_daily_volume=12,
            exploration_rate=0.20,
            metric_warning=None,
        )
        message = build_daily_report(data, "2026-08-30")

        self.assertIn("finance &lt;hot&gt;", message)
        self.assertIn("recipe &amp; food", message)
        self.assertNotIn("finance <hot>", message)

    def test_metric_queries_use_vietnam_calendar_day(self):
        from app.reporting import _category_ranking, _metric_summary

        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                "CREATE TABLE content_posts (facebook_post_id TEXT, category TEXT, published_at TEXT)"
            )
            conn.execute(
                """
                CREATE TABLE content_metrics (
                    facebook_post_id TEXT,
                    score_kind TEXT,
                    content_score REAL,
                    reach INTEGER,
                    impressions INTEGER
                )
                """
            )
            conn.execute(
                "INSERT INTO content_posts VALUES (?, ?, ?)",
                ("post-1", "finance", "2026-08-30T18:30:00+00:00"),
            )
            conn.execute(
                "INSERT INTO content_metrics VALUES (?, ?, ?, ?, ?)",
                ("post-1", "final", 81.0, 1000, None),
            )
            conn.commit()

            def execute(query, params=()):
                return conn.execute(query, params).fetchall()

            count, average, warning = _metric_summary(
                execute, "2026-08-31", "2026-08-31"
            )
            top, bottom = _category_ranking(
                execute, "2026-08-31", "2026-08-31"
            )
        finally:
            conn.close()

        self.assertEqual(count, 1)
        self.assertEqual(average, 81.0)
        self.assertIsNone(warning)
        self.assertEqual(top, ("finance", 81.0))
        self.assertEqual(bottom, ("finance", 81.0))

    def test_metric_warning_detects_partial_exposure_coverage(self):
        from app.reporting import _metric_summary

        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                "CREATE TABLE content_posts (facebook_post_id TEXT, category TEXT, published_at TEXT)"
            )
            conn.execute(
                """
                CREATE TABLE content_metrics (
                    facebook_post_id TEXT,
                    score_kind TEXT,
                    content_score REAL,
                    reach INTEGER,
                    impressions INTEGER
                )
                """
            )
            for index in range(4):
                post_id = f"post-{index}"
                conn.execute(
                    "INSERT INTO content_posts VALUES (?, ?, ?)",
                    (post_id, "finance", "2026-08-30T12:00:00+00:00"),
                )
                has_exposure = index < 2
                conn.execute(
                    "INSERT INTO content_metrics VALUES (?, ?, ?, ?, ?)",
                    (
                        post_id,
                        "final",
                        60.0 + index,
                        1000 if has_exposure else None,
                        1200 if has_exposure else None,
                    ),
                )
            conn.commit()

            def execute(query, params=()):
                return conn.execute(query, params).fetchall()

            count, _, warning = _metric_summary(
                execute, "2026-08-30", "2026-08-30"
            )
        finally:
            conn.close()

        self.assertEqual(count, 4)
        self.assertEqual(warning, "reach/impressions chỉ khả dụng một phần")

    def test_weekly_report_ranks_strategy_dimensions_and_marks_suspended(self):
        from app.reporting import RankedStat, WeeklyReportData, build_weekly_report

        data = WeeklyReportData(
            published=62,
            failed=2,
            average_score=68.4,
            categories=(
                RankedStat("finance", 81.0, 12, "active"),
                RankedStat("fun", 72.0, 10, "active"),
                RankedStat("recipe", 31.0, 8, "suspended"),
            ),
            time_buckets=(
                RankedStat("20:00", 84.0, 9, "active"),
                RankedStat("15:30", 76.0, 7, "active"),
            ),
            hooks=(RankedStat("number_hook", 78.0, 6, "active"),),
            styles=(RankedStat("concise", 74.0, 6, "active"),),
            strategy_version=7,
            metric_warning=None,
        )
        message = build_weekly_report(data, "2026-08-30")

        self.assertIn("finance 81.0", message)
        self.assertIn("recipe 31.0 [sleep]", message)
        self.assertIn("20:00 84.0", message)
        self.assertIn("number_hook 78.0", message)
        self.assertIn("concise 74.0", message)
        self.assertIn("v7", message)

    def test_send_daily_report_uses_vietnam_date_and_fails_if_delivery_fails(self):
        from app.reporting import DailyReportData, send_daily_report

        execute = Mock()
        send = Mock(return_value=False)
        now = datetime(2026, 8, 30, 18, 30, tzinfo=timezone.utc)  # 01:30 VN next day
        data = DailyReportData(
            planned=0,
            published=0,
            skipped=0,
            expired=0,
            failed=0,
            average_score=None,
            top_category=None,
            bottom_category=None,
            strategy_version=None,
            baseline_daily_volume=12,
            exploration_rate=0.20,
            metric_warning=None,
        )

        with self.assertRaisesRegex(RuntimeError, "Telegram daily report delivery failed"):
            send_daily_report(execute, send, now=now, loader=lambda _execute, day: self._assert_day(day, data))

        send.assert_called_once()

    @staticmethod
    def _assert_day(day, data):
        if day != "2026-08-31":
            raise AssertionError(day)
        return data

    def test_send_weekly_report_fails_closed_on_delivery_failure(self):
        from app.reporting import WeeklyReportData, send_weekly_report

        data = WeeklyReportData(
            published=0,
            failed=0,
            average_score=None,
            categories=(),
            time_buckets=(),
            hooks=(),
            styles=(),
            strategy_version=None,
            metric_warning="chưa có dữ liệu",
        )
        with self.assertRaisesRegex(RuntimeError, "Telegram weekly report delivery failed"):
            send_weekly_report(
                Mock(),
                Mock(return_value=False),
                now=datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc),
                loader=lambda _execute, _end: data,
            )


if __name__ == "__main__":
    unittest.main()
