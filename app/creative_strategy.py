from dataclasses import dataclass
import hashlib
import random
import re

from app.publication_context import current_publication_context
from app.selection import (
    exploit_probabilities,
    exploration_probabilities,
    select_mode,
    weighted_choice,
)
from app.strategy_repository import load_config, load_stats
from app.style_registry import ensure_seed_styles, list_active_styles


REGISTRY_DIMENSIONS = {
    "hook": "hook_type",
    "tone": "style_type",
    "cta": "cta_type",
}

HOOK_INSTRUCTIONS = {
    "question": "Mở bài bằng một câu hỏi ngắn, tự nhiên và liên quan trực tiếp đến nội dung.",
    "number": "Mở bài bằng cấu trúc có con số hoặc số lượng chỉ khi dữ kiện đầu vào hỗ trợ; không bịa số liệu.",
    "surprising_fact": "Mở bằng một chi tiết đáng chú ý có thật trong dữ kiện đầu vào; tuyệt đối không bịa sự kiện hay số liệu để gây sốc.",
    "direct_statement": "Mở bài bằng một nhận định trực tiếp, rõ ý, không vòng vo và không phóng đại.",
    "contrast": "Mở bài bằng một tương phản có thật giữa hai ý trong dữ kiện, không tạo đối lập giả.",
    "curiosity": "Mở bài gợi tò mò vừa đủ nhưng phải đúng dữ kiện, không giật tít sai lệch.",
}

STYLE_INSTRUCTIONS = {
    "concise_news": "Giọng ngắn gọn kiểu bản tin: câu rõ, ưu tiên thông tin chính, tránh lan man.",
    "conversational": "Giọng trò chuyện tự nhiên, gần gũi nhưng không suồng sã quá mức.",
    "witty": "Giọng dí dỏm có kiểm soát; sự hài hước không được làm sai hoặc che khuất thông tin chính.",
    "explanatory": "Giọng giải thích dễ hiểu, nối nguyên nhân-kết quả rõ ràng và tránh thuật ngữ không cần thiết.",
    "reflective": "Giọng suy ngẫm, chậm rãi và có chiều sâu nhưng vẫn súc tích.",
}

CTA_INSTRUCTIONS = {
    "opinion_question": "Kết bài bằng đúng một câu hỏi mở mời người đọc nêu quan điểm.",
    "choose_side": "Kết bài bằng một lựa chọn hai phía phù hợp nội dung và hỏi người đọc nghiêng về phía nào.",
    "experience_share": "Kết bài bằng lời mời người đọc chia sẻ trải nghiệm thực tế của họ.",
    "save_for_later": "Kết bài bằng lời gợi ý lưu lại nếu thấy hữu ích, không thúc ép tương tác.",
    "no_cta": "Không thêm lời kêu gọi bình luận, chia sẻ, lưu bài hoặc câu hỏi tương tác ở cuối.",
}


@dataclass(frozen=True)
class CreativeProfile:
    hook_type: str
    style_type: str
    cta_type: str


def _stable_rng(
    run_key: str,
    category: str,
    strategy_version: int | None,
    scope: str,
) -> random.Random:
    raw = f"{run_key}:{category}:{strategy_version if strategy_version is not None else 'baseline'}:{scope}"
    seed = int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16], 16)
    return random.Random(seed)


def _fallback_value(variants, rng: random.Random) -> str:
    preferred = [variant.value for variant in variants if variant.status in {"baseline", "active"}]
    values = preferred or [variant.value for variant in variants]
    if not values:
        raise RuntimeError("No registered creative variants are available")
    return values[rng.randrange(len(values))]


def _select_dimension(
    execute_fn,
    *,
    registry_dimension: str,
    stat_dimension: str,
    requested_mode: str,
    run_key: str,
    category: str,
    strategy_version: int | None,
) -> str:
    variants = list_active_styles(execute_fn, registry_dimension)
    if not variants:
        raise RuntimeError(f"No active creative variants for {registry_dimension}")

    registered_values = [variant.value for variant in variants]
    registered = set(registered_values)
    stats = [
        stat
        for stat in load_stats(execute_fn, stat_dimension)
        if stat.value in registered
    ]
    rng = _stable_rng(run_key, category, strategy_version, registry_dimension)

    if requested_mode == "explore":
        probabilities = exploration_probabilities(stats, registered_values)
        if probabilities:
            return weighted_choice(list(probabilities.items()), rng)

    probabilities = exploit_probabilities(stats)
    if probabilities:
        return weighted_choice(list(probabilities.items()), rng)

    return _fallback_value(variants, rng)


def select_creative_profile(
    execute_fn,
    *,
    run_key: str,
    category: str,
    strategy_version: int | None,
) -> CreativeProfile:
    """Choose one retry-stable creative profile from registered safe variants."""
    ensure_seed_styles(execute_fn)
    config = load_config(execute_fn)
    mode_rng = _stable_rng(run_key, category, strategy_version, "profile-mode")
    requested_mode = select_mode(mode_rng, config.exploration_rate)

    selected = {}
    for registry_dimension, stat_dimension in REGISTRY_DIMENSIONS.items():
        selected[stat_dimension] = _select_dimension(
            execute_fn,
            registry_dimension=registry_dimension,
            stat_dimension=stat_dimension,
            requested_mode=requested_mode,
            run_key=run_key,
            category=category,
            strategy_version=strategy_version,
        )

    return CreativeProfile(
        hook_type=selected["hook_type"],
        style_type=selected["style_type"],
        cta_type=selected["cta_type"],
    )


def creative_prompt_suffix(profile: CreativeProfile) -> str:
    hook = HOOK_INSTRUCTIONS.get(
        profile.hook_type,
        "Mở bài tự nhiên, rõ ý và chỉ dùng dữ kiện đã được cung cấp.",
    )
    style = STYLE_INSTRUCTIONS.get(
        profile.style_type,
        "Giữ văn phong rõ ràng, tự nhiên và phù hợp ngữ cảnh.",
    )
    cta = CTA_INSTRUCTIONS.get(
        profile.cta_type,
        "Không ép tương tác; kết bài tự nhiên theo nội dung.",
    )
    return (
        "\nCHỈ DẪN PHONG CÁCH ADAPTIVE:\n"
        f"- Hook: {hook}\n"
        f"- Phong cách: {style}\n"
        f"- CTA: {cta}\n"
        "- Toàn bộ nội dung phải bám dữ kiện đầu vào; không bịa thông tin, sự kiện, con số hoặc nguồn."
    )


def current_creative_prompt_suffix() -> str:
    context = current_publication_context()
    if context is None:
        return ""
    hook_type = getattr(context, "hook_type", "unknown")
    style_type = getattr(context, "style_type", "unknown")
    cta_type = getattr(context, "cta_type", "none")
    if hook_type == "unknown" and style_type == "unknown" and cta_type == "none":
        return ""
    return creative_prompt_suffix(
        CreativeProfile(
            hook_type=hook_type,
            style_type=style_type,
            cta_type=cta_type,
        )
    )


def _remove_legacy_conflicts(text: str, matched_marker: str) -> str:
    """Remove only fixed legacy instructions that contradict adaptive controls."""
    if matched_marker == "Viết status FB cho tin:":
        text = text.replace(
            "Tóm tắt sắc sảo, hóm hỉnh, <250 chữ. Kết bài bằng 1 câu hỏi.",
            "Tóm tắt <250 chữ theo chỉ dẫn phong cách adaptive bên dưới. "
            "Kết bài theo chỉ dẫn CTA adaptive bên dưới.",
        )
    elif matched_marker == "cập nhật tỷ giá ngoại tệ":
        text = text.replace(
            "Giọng điệu chuyên nghiệp, nhận định nhanh gọn. "
            "Kết thúc bằng câu hỏi mở về diễn biến thị trường và hashtag",
            "Trình bày ngắn gọn theo chỉ dẫn phong cách adaptive bên dưới. "
            "Kết bài theo chỉ dẫn CTA adaptive bên dưới. Giữ hashtag",
        )
    elif matched_marker == "status tấu hài":
        text = re.sub(r"(?m)^Văn phong bắt buộc:[^\n]*(?:\n|$)", "", text)
    return text


def run_with_creative_prompt(job_fn, gemini_module, *, markers: tuple[str, ...]):
    """Append creative guidance only to the job's primary content-generation prompt."""
    original = gemini_module.call_gemini

    def targeted(prompt, *args, **kwargs):
        text = str(prompt)
        suffix = current_creative_prompt_suffix()
        matched_marker = next((marker for marker in markers if marker in text), None)
        if suffix and matched_marker is not None:
            text = _remove_legacy_conflicts(text, matched_marker)
            text = text + suffix
        return original(text, *args, **kwargs)

    gemini_module.call_gemini = targeted
    try:
        return job_fn()
    finally:
        gemini_module.call_gemini = original
