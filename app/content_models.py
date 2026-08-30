from dataclasses import dataclass


@dataclass(frozen=True)
class ContentCandidate:
    category: str
    topic_key: str
    topic_text: str
    content_text: str
    source_url: str | None = None
    source_title: str | None = None
    hook_type: str = "unknown"
    style_type: str = "unknown"
    cta_type: str = "none"
    format_type: str = "text"
    style_experiment_key: str | None = None


@dataclass(frozen=True)
class RecentContent:
    id: int
    category: str
    topic_key: str
    topic_text: str
    content_text: str
    source_url: str | None
    published_at: str | None


@dataclass(frozen=True)
class DuplicateDecision:
    duplicate: bool
    score: float
    layer: str
    reason: str
