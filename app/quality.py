import json
from dataclasses import dataclass
from typing import Iterable

from app.content_models import ContentCandidate, RecentContent


QUALITY_RECENT_LIMIT = 12


@dataclass(frozen=True)
class QualityRubric:
    novelty: float
    hook: float
    usefulness: float
    readability: float
    tone: float
    cta: float


@dataclass(frozen=True)
class QualityDecision:
    score: float
    action: str
    reasons: tuple[str, ...] = ()


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return min(high, max(low, float(value)))


def combine_quality_score(rubric: QualityRubric, penalties: Iterable[float]) -> float:
    weighted = (
        _clamp(rubric.novelty) * 0.25
        + _clamp(rubric.hook) * 0.20
        + _clamp(rubric.usefulness) * 0.20
        + _clamp(rubric.readability) * 0.15
        + _clamp(rubric.tone) * 0.10
        + _clamp(rubric.cta) * 0.10
    )
    score = weighted + sum(float(penalty) for penalty in penalties)
    return round(_clamp(score), 2)


def decision_for_score(score: float, reasons: tuple[str, ...] = ()) -> QualityDecision:
    normalized = _clamp(score)
    if normalized >= 75.0:
        action = "publish"
    elif normalized >= 65.0:
        action = "rewrite"
    else:
        action = "reject"
    return QualityDecision(round(normalized, 2), action, reasons)


def _parse_json_object(raw: str) -> dict:
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
        raise ValueError("quality response must be a JSON object")
    return parsed


def _required_bool(data: dict, key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be boolean")
    return value


def assess_draft(
    candidate: ContentCandidate,
    recent: list[RecentContent],
    gemini_fn,
) -> QualityDecision:
    bounded_recent = recent[:QUALITY_RECENT_LIMIT]
    recent_lines = "\n".join(
        f"- hook={item.topic_key}; topic={item.topic_text}; content={item.content_text[:160]}"
        for item in bounded_recent
    )
    prompt = (
        "Assess this Facebook draft. Return JSON only with numeric fields novelty, hook, "
        "usefulness, readability, tone, cta (0-100); boolean fields semantic_duplicate, "
        "hook_too_similar, excessive_clickbait, repetitive_cta, format_length_violation; "
        "and a short string reason.\n"
        f"Category: {candidate.category}\n"
        f"Topic: {candidate.topic_text}\n"
        f"Hook type: {candidate.hook_type}\n"
        f"Style: {candidate.style_type}\n"
        f"CTA: {candidate.cta_type}\n"
        f"Format: {candidate.format_type}\n"
        f"Draft: {candidate.content_text}\n"
        "Recent examples:\n"
        f"{recent_lines or '- none'}"
    )

    try:
        raw = gemini_fn(prompt)
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("empty quality response")
        data = _parse_json_object(raw)

        rubric = QualityRubric(
            novelty=float(data["novelty"]),
            hook=float(data["hook"]),
            usefulness=float(data["usefulness"]),
            readability=float(data["readability"]),
            tone=float(data["tone"]),
            cta=float(data["cta"]),
        )
        reason = data.get("reason")
        if not isinstance(reason, str):
            raise ValueError("reason must be string")

        penalty_rules = (
            ("semantic_duplicate", -40.0),
            ("hook_too_similar", -20.0),
            ("excessive_clickbait", -20.0),
            ("repetitive_cta", -10.0),
            ("format_length_violation", -10.0),
        )
        penalties = []
        reasons = []
        for key, penalty in penalty_rules:
            if _required_bool(data, key):
                penalties.append(penalty)
                reasons.append(key)

        score = combine_quality_score(rubric, penalties)
        if reason.strip():
            reasons.append(reason.strip())
        return decision_for_score(score, tuple(reasons))
    except Exception:
        return QualityDecision(
            score=65.0,
            action="rewrite",
            reasons=("quality_assessment_unavailable",),
        )
