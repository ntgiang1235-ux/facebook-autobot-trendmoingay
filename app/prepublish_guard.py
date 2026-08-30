from dataclasses import dataclass, replace
from datetime import datetime, timezone

from app.content_models import ContentCandidate, RecentContent
from app.content_pipeline import prepare_publishable_candidate
from app.content_repository import content_hash


@dataclass(frozen=True)
class PrePublishDecision:
    publish: bool
    status: str
    request_data: dict | None
    quality_score: float | None
    duplicate_score: float | None
    rewrite_count: int
    detail: str
    candidate: ContentCandidate | None = None


def _format_type(endpoint: str) -> str:
    if endpoint == "me/photos":
        return "photo"
    return "text"


def _candidate_from_request(action: str, endpoint: str, request_data: dict) -> ContentCandidate | None:
    message = request_data.get("message")
    if not isinstance(message, str) or not message.strip():
        return None

    content = message.strip()
    source_url = request_data.get("link")
    if not isinstance(source_url, str) or not source_url.strip():
        source_url = None
    else:
        source_url = source_url.strip()

    return ContentCandidate(
        category=action,
        topic_key=content_hash(source_url or content),
        topic_text=content,
        content_text=content,
        source_url=source_url,
        format_type=_format_type(endpoint),
    )


def _rewrite_candidate(candidate: ContentCandidate, quality, gemini_fn) -> ContentCandidate:
    reasons = ", ".join(quality.reasons) or "quality below threshold"
    prompt = (
        "Rewrite this Facebook post so it is clearer, more useful, less repetitive, and has a stronger hook. "
        "Preserve all factual meaning, source claims, URLs, hashtags, and important numbers. "
        "Do not invent facts. Return only the rewritten post text, no explanation.\n"
        f"Category: {candidate.category}\n"
        f"Quality issues: {reasons}\n"
        f"Original post:\n{candidate.content_text}"
    )
    rewritten = gemini_fn(prompt)
    if not isinstance(rewritten, str) or not rewritten.strip():
        return candidate
    text = rewritten.strip()
    return replace(
        candidate,
        topic_text=text,
        content_text=text,
        topic_key=content_hash(candidate.source_url or text),
    )


def evaluate_request(
    *,
    action: str,
    endpoint: str,
    request_data: dict,
    recent: list[RecentContent],
    gemini_fn,
    now: datetime | None = None,
) -> PrePublishDecision:
    """Run the approved dedup + quality pipeline before a Facebook publish request."""
    del now  # reserved for future deterministic policy windows; recent is already bounded by caller.
    candidate = _candidate_from_request(action, endpoint, dict(request_data))
    if candidate is None:
        return PrePublishDecision(
            publish=False,
            status="skipped_low_quality",
            request_data=None,
            quality_score=None,
            duplicate_score=None,
            rewrite_count=0,
            detail="missing publishable message",
            candidate=None,
        )

    result = prepare_publishable_candidate(
        candidate,
        recent,
        gemini_fn,
        lambda current, quality: _rewrite_candidate(current, quality, gemini_fn),
        max_rewrites=2,
    )

    if result.status != "ready":
        return PrePublishDecision(
            publish=False,
            status=result.status,
            request_data=None,
            quality_score=result.quality_score,
            duplicate_score=result.duplicate_score,
            rewrite_count=result.rewrite_count,
            detail=result.detail,
            candidate=result.candidate,
        )

    outbound = dict(request_data)
    outbound["message"] = result.candidate.content_text
    return PrePublishDecision(
        publish=True,
        status="ready",
        request_data=outbound,
        quality_score=result.quality_score,
        duplicate_score=result.duplicate_score,
        rewrite_count=result.rewrite_count,
        detail=result.detail,
        candidate=result.candidate,
    )
