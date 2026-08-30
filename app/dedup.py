import re
import unicodedata
from difflib import SequenceMatcher

from app.content_models import ContentCandidate, DuplicateDecision, RecentContent


CATEGORY_WINDOWS = {
    "news": 7,
    "post": 7,
    "finance": 14,
    "fun": 14,
    "recipe": 30,
    "philosophy": 30,
    "video": 30,
}


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def lexical_similarity(a: str, b: str) -> float:
    normalized_a = normalize_text(a)
    normalized_b = normalize_text(b)
    if not normalized_a or not normalized_b:
        return 0.0
    return SequenceMatcher(None, normalized_a, normalized_b).ratio()


def anti_repeat_days(category: str) -> int:
    return CATEGORY_WINDOWS.get(category, 14)


def check_local_duplicate(
    candidate: ContentCandidate,
    recent: list[RecentContent],
    threshold: float = 0.80,
) -> DuplicateDecision | None:
    for item in recent:
        if (
            candidate.source_url
            and item.source_url
            and candidate.source_url == item.source_url
        ):
            return DuplicateDecision(True, 1.0, "exact", "same source URL")

        if (
            candidate.topic_key
            and item.topic_key
            and candidate.topic_key == item.topic_key
        ):
            return DuplicateDecision(True, 1.0, "exact", "same topic key")

    best_score = 0.0
    for item in recent:
        score = lexical_similarity(candidate.topic_text, item.topic_text)
        if score > best_score:
            best_score = score

    if best_score >= threshold:
        return DuplicateDecision(True, best_score, "lexical", "similar topic text")

    return None
