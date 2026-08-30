from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html import escape
from typing import Callable
from zoneinfo import ZoneInfo

from app import strategy_repository
from app.job_contract import JobOutcome, success


VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


@dataclass(frozen=True)
class RankedStat:
    value: str
    score: float
    sample_count: int
    status: str


@dataclass(frozen=True)
class DailyReportData:
    planned: int
    published: int
    skipped: int
    expired: int
    failed: int
    average_score: float | None
    top_category: tuple[str, float] | None
    bottom_category: tuple[str, float] | None
    strategy_version: int | None
    baseline_daily_volume: int
    exploration_rate: float
    metric_warning: str | None


@dataclass(frozen=True)
class WeeklyReportData:
    published: int
    failed: int
    average_score: float | None
    categories: tuple[RankedStat, ...]
    time_buckets: tuple[RankedStat, ...]
    hooks: tuple[RankedStat, ...]
    styles: tuple[RankedStat, ...]
    strategy_version: int | None
    metric_warning: str | None


def _as_utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _vietnam_date(value: datetime | None) -> date:
    return _as_utc(value).astimezone(VIETNAM_TZ).date()


def _score_text(value: float | None) -> str:
    return "chưa đủ dữ liệu" if value is None else f"{value:.1f}"


def _rank_text(value: tuple[str, float] | None) -> str:
    if value is None:
        return "chưa đủ dữ liệu"
    label, score = value
    return f"{escape(str(label))} {float(score):.1f}"


def _format_ranked(values: tuple[RankedStat, ...], *, limit: int = 3) -> str:
    if not values:
        return "chưa đủ dữ liệu"
    parts = []
    for stat in values[:limit]:
        sleeping = " [sleep]" if stat.status == "suspended" else ""
        parts.append(f"{escape(stat.value)} {stat.score:.1f}{sleeping}")
    return " · ".join(parts)


def build_daily_report(data: DailyReportData, report_date: str) -> str:
    strategy = f"v{data.strategy_version}" if data.strategy_version is not None else "baseline"
    lines = [
        f"📊 <b>TREND MỖI NGÀY — DAILY</b> · {escape(report_date)}",
        (
            f"Kế hoạch {data.planned} | đăng {data.published} | bỏ {data.skipped} | "
            f"hết hạn {data.expired} | lỗi {data.failed}"
        ),
        f"Điểm hiệu quả: {_score_text(data.average_score)}",
        f"Top: {_rank_text(data.top_category)} | Thấp: {_rank_text(data.bottom_category)}",
        (
            f"Adaptive: {strategy} | baseline {data.baseline_daily_volume} | "
            f"explore {data.exploration_rate * 100:.0f}%"
        ),
    ]
    if data.metric_warning:
        lines.append(f"Metrics: {escape(data.metric_warning)}")
    return "\n".join(lines)


def build_weekly_report(data: WeeklyReportData, report_date: str) -> str:
    strategy = f"v{data.strategy_version}" if data.strategy_version is not None else "baseline"
    lines = [
        f"📈 <b>TREND MỖI NGÀY — WEEKLY</b> · {escape(report_date)}",
        f"7 ngày: đăng {data.published} | lỗi {data.failed} | score {_score_text(data.average_score)}",
        f"Category: {_format_ranked(data.categories)}",
        f"Giờ: {_format_ranked(data.time_buckets)}",
        f"Hook: {_format_ranked(data.hooks)}",
        f"Style: {_format_ranked(data.styles)}",
        f"Strategy: {strategy}",
    ]
    if data.metric_warning:
        lines.append(f"Metrics: {escape(data.metric_warning)}")
    return "\n".join(lines)


def _metric_summary(execute_fn, start_date: str, end_date: str):
    rows = execute_fn(
        """
        SELECT COUNT(*), AVG(cm.content_score),
               SUM(CASE WHEN cm.reach IS NOT NULL THEN 1 ELSE 0 END),
               SUM(CASE WHEN cm.impressions IS NOT NULL THEN 1 ELSE 0 END)
        FROM content_metrics cm
        JOIN content_posts cp ON cp.facebook_post_id = cm.facebook_post_id
        WHERE cm.score_kind = 'final'
          AND cm.content_score IS NOT NULL
          AND date(cp.published_at) BETWEEN date(?) AND date(?)
        """,
        (start_date, end_date),
    )
    if not rows:
        return 0, None, "chưa có final metrics"

    count = int(rows[0][0] or 0)
    average = float(rows[0][1]) if rows[0][1] is not None else None
    reach_count = int(rows[0][2] or 0)
    impression_count = int(rows[0][3] or 0)
    if count == 0:
        warning = "chưa có final metrics"
    elif reach_count == 0 and impression_count == 0:
        warning = "reach/impressions chưa khả dụng; đang dùng engagement fallback"
    elif reach_count + impression_count < count:
        warning = "reach/impressions chỉ khả dụng một phần"
    else:
        warning = None
    return count, average, warning


def _category_ranking(execute_fn, start_date: str, end_date: str):
    rows = execute_fn(
        """
        SELECT cp.category, AVG(cm.content_score) AS avg_score
        FROM content_metrics cm
        JOIN content_posts cp ON cp.facebook_post_id = cm.facebook_post_id
        WHERE cm.score_kind = 'final'
          AND cm.content_score IS NOT NULL
          AND date(cp.published_at) BETWEEN date(?) AND date(?)
        GROUP BY cp.category
        ORDER BY avg_score DESC, cp.category ASC
        """,
        (start_date, end_date),
    )
    values = [(str(row[0]), float(row[1])) for row in rows if row[1] is not None]
    top = values[0] if values else None
    bottom = values[-1] if values else None
    return top, bottom


def load_daily_report(execute_fn, report_date: str) -> DailyReportData:
    plan_rows = execute_fn(
        """
        SELECT status, COUNT(*)
        FROM daily_plan
        WHERE plan_date = ?
        GROUP BY status
        """,
        (report_date,),
    )
    counts = {str(status): int(count) for status, count in plan_rows}
    planned = sum(counts.values())

    end = date.fromisoformat(report_date)
    start = end - timedelta(days=6)
    _, average, warning = _metric_summary(execute_fn, start.isoformat(), end.isoformat())
    top, bottom = _category_ranking(execute_fn, start.isoformat(), end.isoformat())
    config = strategy_repository.load_config(execute_fn)

    return DailyReportData(
        planned=planned,
        published=counts.get("published", 0),
        skipped=counts.get("skipped", 0),
        expired=counts.get("expired", 0),
        failed=counts.get("failed", 0),
        average_score=average,
        top_category=top,
        bottom_category=bottom,
        strategy_version=config.current_strategy_version,
        baseline_daily_volume=config.baseline_daily_volume,
        exploration_rate=config.exploration_rate,
        metric_warning=warning,
    )


def _ranked_strategy(execute_fn, dimension: str) -> tuple[RankedStat, ...]:
    stats = strategy_repository.load_stats(execute_fn, dimension)
    ranked = sorted(
        (
            RankedStat(
                value=stat.value,
                score=stat.weighted_score_14d,
                sample_count=stat.sample_count,
                status=stat.status,
            )
            for stat in stats
            if stat.status != "retired"
        ),
        key=lambda item: (-item.score, item.value),
    )
    return tuple(ranked[:3])


def load_weekly_report(execute_fn, end_date: str) -> WeeklyReportData:
    end = date.fromisoformat(end_date)
    start = end - timedelta(days=6)
    outcome_rows = execute_fn(
        """
        SELECT status, COUNT(*)
        FROM daily_plan
        WHERE plan_date BETWEEN ? AND ?
        GROUP BY status
        """,
        (start.isoformat(), end.isoformat()),
    )
    counts = {str(status): int(count) for status, count in outcome_rows}
    _, average, warning = _metric_summary(execute_fn, start.isoformat(), end.isoformat())
    config = strategy_repository.load_config(execute_fn)

    return WeeklyReportData(
        published=counts.get("published", 0),
        failed=counts.get("failed", 0),
        average_score=average,
        categories=_ranked_strategy(execute_fn, "category"),
        time_buckets=_ranked_strategy(execute_fn, "time_bucket"),
        hooks=_ranked_strategy(execute_fn, "hook"),
        styles=_ranked_strategy(execute_fn, "style"),
        strategy_version=config.current_strategy_version,
        metric_warning=warning,
    )


def send_daily_report(
    execute_fn,
    send_fn: Callable[[str], bool],
    *,
    now: datetime | None = None,
    loader=load_daily_report,
) -> JobOutcome:
    report_date = _vietnam_date(now).isoformat()
    data = loader(execute_fn, report_date)
    if not send_fn(build_daily_report(data, report_date)):
        raise RuntimeError("Telegram daily report delivery failed")
    return success(f"daily report sent: {report_date}")


def send_weekly_report(
    execute_fn,
    send_fn: Callable[[str], bool],
    *,
    now: datetime | None = None,
    loader=load_weekly_report,
) -> JobOutcome:
    report_date = _vietnam_date(now).isoformat()
    data = loader(execute_fn, report_date)
    if not send_fn(build_weekly_report(data, report_date)):
        raise RuntimeError("Telegram weekly report delivery failed")
    return success(f"weekly report sent: {report_date}")
