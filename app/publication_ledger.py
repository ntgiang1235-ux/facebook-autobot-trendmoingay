import hashlib
from datetime import datetime, timezone

from app import content_repository
from app.content_models import ContentCandidate
from app.publication_context import PublicationContext, current_publication_context


def _published_at(value: datetime | None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


def _topic_key(category: str, source_url: str | None, content_text: str) -> str:
    seed = source_url or content_text
    digest = hashlib.sha256(seed.strip().lower().encode("utf-8")).hexdigest()[:20]
    return f"{category}:{digest}"


def record_published_content(
    execute_fn,
    *,
    action: str,
    endpoint: str,
    request_data: dict,
    response: dict,
    context: PublicationContext | None = None,
    now: datetime | None = None,
    topic_text: str | None = None,
    source_url: str | None = None,
    source_title: str | None = None,
    format_type: str | None = None,
) -> int | None:
    """Persist one confirmed Facebook publish into the canonical content ledger.

    Only explicit publication metadata is stored. Legacy jobs that do not expose
    hook/style/CTA metadata retain the model defaults rather than inventing values.
    """
    post_id = response.get("post_id") or response.get("id")
    if not post_id:
        raise RuntimeError("Facebook post id missing from successful publish")

    safe_request = {
        key: value
        for key, value in dict(request_data or {}).items()
        if key != "access_token"
    }
    content_text = str(
        safe_request.get("message") or safe_request.get("description") or ""
    ).strip()
    if not content_text:
        raise RuntimeError("Published content text is missing")

    active_context = context or current_publication_context()
    category = (
        active_context.category
        if active_context is not None and active_context.category
        else action
    )
    explicit_source = source_url
    if explicit_source is None and endpoint.endswith("/feed"):
        link = safe_request.get("link")
        explicit_source = str(link).strip() if link else None

    resolved_format = format_type or (
        "photo" if endpoint.endswith("/photos") else "text"
    )
    resolved_topic = (topic_text or source_title or content_text[:240]).strip()

    candidate = ContentCandidate(
        category=category,
        topic_key=_topic_key(category, explicit_source, content_text),
        topic_text=resolved_topic,
        content_text=content_text,
        source_url=explicit_source,
        source_title=source_title,
        format_type=resolved_format,
    )

    return content_repository.record_candidate(
        execute_fn,
        candidate,
        run_key=active_context.run_key if active_context is not None else None,
        status="published",
        facebook_post_id=str(post_id),
        action=action,
        scheduled_for=(
            active_context.scheduled_for if active_context is not None else None
        ),
        published_at=_published_at(now),
        strategy_mode=(
            active_context.strategy_mode if active_context is not None else "baseline"
        ),
        strategy_version=(
            active_context.strategy_version if active_context is not None else None
        ),
    )
