from dataclasses import dataclass

from app.content_models import ContentCandidate, RecentContent
from app.dedup import check_local_duplicate, check_semantic_duplicate
from app.quality import assess_draft


@dataclass(frozen=True)
class PipelineResult:
    status: str
    candidate: ContentCandidate
    quality_score: float | None
    duplicate_score: float | None
    detail: str
    rewrite_count: int


def prepare_publishable_candidate(
    candidate: ContentCandidate,
    recent: list[RecentContent],
    gemini_fn,
    rewrite_fn,
    *,
    max_rewrites: int = 2,
) -> PipelineResult:
    """Validate a draft for publication without publishing it."""
    rewrite_limit = max(0, int(max_rewrites))
    current = candidate
    rewrite_count = 0

    while True:
        local = check_local_duplicate(current, recent)
        if local is not None and local.duplicate:
            return PipelineResult(
                status="rejected_duplicate",
                candidate=current,
                quality_score=None,
                duplicate_score=local.score,
                detail=local.reason,
                rewrite_count=rewrite_count,
            )

        semantic = check_semantic_duplicate(current, recent, gemini_fn)
        if semantic.duplicate:
            return PipelineResult(
                status="rejected_duplicate",
                candidate=current,
                quality_score=None,
                duplicate_score=semantic.score,
                detail=semantic.reason,
                rewrite_count=rewrite_count,
            )

        quality = assess_draft(current, recent, gemini_fn)
        if quality.action == "publish":
            return PipelineResult(
                status="ready",
                candidate=current,
                quality_score=quality.score,
                duplicate_score=semantic.score,
                detail="; ".join(quality.reasons),
                rewrite_count=rewrite_count,
            )

        if quality.action == "reject":
            return PipelineResult(
                status="skipped_low_quality",
                candidate=current,
                quality_score=quality.score,
                duplicate_score=semantic.score,
                detail="; ".join(quality.reasons) or "quality score below threshold",
                rewrite_count=rewrite_count,
            )

        if rewrite_count >= rewrite_limit:
            return PipelineResult(
                status="skipped_low_quality",
                candidate=current,
                quality_score=quality.score,
                duplicate_score=semantic.score,
                detail="maximum rewrite attempts reached",
                rewrite_count=rewrite_count,
            )

        current = rewrite_fn(current, quality)
        if not isinstance(current, ContentCandidate):
            raise TypeError("rewrite_fn must return ContentCandidate")
        rewrite_count += 1
