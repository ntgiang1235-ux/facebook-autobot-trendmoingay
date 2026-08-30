import math
from dataclasses import dataclass


GRAPH_BASE = "https://graph.facebook.com/v25.0"
INSIGHT_METRICS = {
    "reach": "post_impressions_unique",
    "impressions": "post_impressions",
    "video_views": "post_video_views",
}


class FacebookMetricsError(RuntimeError):
    pass


@dataclass(frozen=True)
class CollectedMetrics:
    reactions: int | None
    comments: int | None
    shares: int | None
    reach: int | None
    impressions: int | None
    video_views: int | None
    follower_delta: int | None
    capabilities: frozenset[str]


def _nonnegative_count(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        return None
    return int(numeric)


def _summary_count(payload: dict, field: str) -> int | None:
    item = payload.get(field)
    if not isinstance(item, dict):
        return None
    summary = item.get("summary")
    if not isinstance(summary, dict):
        return None
    return _nonnegative_count(summary.get("total_count"))


def _share_count(payload: dict) -> int | None:
    shares = payload.get("shares")
    if not isinstance(shares, dict):
        return None
    return _nonnegative_count(shares.get("count"))


def _insight_value(payload: dict) -> int | None:
    data = payload.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None
    item = data[0]
    if "value" in item:
        return _nonnegative_count(item.get("value"))
    values = item.get("values")
    if not isinstance(values, list) or not values or not isinstance(values[0], dict):
        return None
    return _nonnegative_count(values[0].get("value"))


def collect_post_metrics(http, post_id: str, access_token: str) -> CollectedMetrics:
    response = http.get(
        f"{GRAPH_BASE}/{post_id}",
        params={
            "fields": "reactions.limit(0).summary(true),comments.limit(0).summary(true),shares",
            "access_token": access_token,
        },
        timeout=20,
    )
    try:
        payload = response.json()
    except Exception as exc:
        raise FacebookMetricsError("Facebook post metrics trả JSON không hợp lệ") from exc
    if not response.ok or not isinstance(payload, dict) or "error" in payload:
        raise FacebookMetricsError(
            f"Facebook post metrics lỗi HTTP {response.status_code}: {payload}"
        )

    reactions = _summary_count(payload, "reactions")
    comments = _summary_count(payload, "comments")
    shares = _share_count(payload)
    capabilities = {
        name
        for name, value in (
            ("reactions", reactions),
            ("comments", comments),
            ("shares", shares),
        )
        if value is not None
    }

    insight_values: dict[str, int | None] = {
        "reach": None,
        "impressions": None,
        "video_views": None,
    }
    for capability, metric_name in INSIGHT_METRICS.items():
        try:
            insight_response = http.get(
                f"{GRAPH_BASE}/{post_id}/insights",
                params={"metric": metric_name, "access_token": access_token},
                timeout=20,
            )
            if not insight_response.ok:
                continue
            insight_payload = insight_response.json()
            if not isinstance(insight_payload, dict) or "error" in insight_payload:
                continue
            value = _insight_value(insight_payload)
            if value is None:
                continue
            insight_values[capability] = value
            capabilities.add(capability)
        except Exception:
            continue

    return CollectedMetrics(
        reactions=reactions,
        comments=comments,
        shares=shares,
        reach=insight_values["reach"],
        impressions=insight_values["impressions"],
        video_views=insight_values["video_views"],
        follower_delta=None,
        capabilities=frozenset(capabilities),
    )
