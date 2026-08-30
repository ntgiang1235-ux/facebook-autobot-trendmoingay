import json
import math
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
SEMANTIC_RECENT_LIMIT = 20


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


def _parse_semantic_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("semantic response must be a JSON object")
    return parsed


def check_semantic_duplicate(
    candidate: ContentCandidate,
    recent: list[RecentContent],
    gemini_fn,
    limit: int = SEMANTIC_RECENT_LIMIT,
) -> DuplicateDecision:
    """Run a bounded semantic check after exact/lexical checks are inconclusive."""
    try:
        requested_limit = int(limit)
    except (TypeError, ValueError):
        requested_limit = SEMANTIC_RECENT_LIMIT
    bounded_limit = min(max(requested_limit, 0), SEMANTIC_RECENT_LIMIT)
    bounded_recent = recent[:bounded_limit]

    recent_lines = "\n".join(
        f"- topic_key={item.topic_key}; topic={item.topic_text}"
        for item in bounded_recent
    )
    prompt = (
        "Compare the candidate topic with the recent topics and decide whether "
        "they describe the same underlying event or substantially the same topic.\n"
        "Return JSON only with exactly these keys: duplicate (boolean), "
        "similarity (number from 0 to 1), reason (short string).\n"
        f"Candidate topic_key={candidate.topic_key}; topic={candidate.topic_text}\n"
        "Recent topics:\n"
        f"{recent_lines or '- none'}"
    )

    try:
        raw = gemini_fn(prompt)
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("empty semantic response")
        data = _parse_semantic_json(raw)

        duplicate = data.get("duplicate")
        if not isinstance(duplicate, bool):
            raise ValueError("duplicate must be boolean")

        similarity = float(data.get("similarity"))
        if not math.isfinite(similarity):
            raise ValueError("similarity must be finite")
        similarity = min(1.0, max(0.0, similarity))

        reason = data.get("reason")
        if not isinstance(reason, str):
            raise ValueError("reason must be string")

        return DuplicateDecision(duplicate, similarity, "semantic", reason.strip())
    except Exception:
        return DuplicateDecision(
            False,
            0.0,
            "semantic_unavailable",
            "semantic check unavailable",
        )
