import random
from dataclasses import dataclass
from datetime import datetime, timezone

from app import selection, strategy_repository, style_registry
from app.strategy_models import StrategyStat


_DIMENSIONS = (
    ("hook", "hook_type"),
    ("tone", "style_type"),
    ("cta", "cta_type"),
)

_HOOK_GUIDANCE = {
    "question": "mở bằng một câu hỏi tự nhiên, liên quan trực tiếp nội dung",
    "number": "mở bằng một con số hoặc số lượng điểm đáng chú ý có căn cứ trong bài",
    "surprising_fact": "mở bằng chi tiết bất ngờ nhưng không phóng đại hay bịa dữ kiện",
    "direct_statement": "mở thẳng vào thông tin quan trọng nhất, không vòng vo",
    "contrast": "mở bằng một đối lập rõ ràng để tạo nhịp đọc",
    "curiosity": "gợi tò mò vừa đủ, không dùng clickbait gây hiểu sai",
}

_TONE_GUIDANCE = {
    "concise_news": "ngắn gọn, rõ dữ kiện, giống bản tin mạng xã hội hiện đại",
    "conversational": "gần gũi, tự nhiên như đang trò chuyện với người đọc",
    "witty": "hóm hỉnh có kiểm soát, không làm sai lệch thông tin",
    "explanatory": "giải thích dễ hiểu, ưu tiên giá trị thực tế",
    "reflective": "điềm tĩnh, gợi suy nghĩ, tránh sáo rỗng",
}

_CTA_GUIDANCE = {
    "opinion_question": "kết bằng một câu hỏi xin ý kiến phù hợp nội dung",
    "choose_side": "kết bằng lời mời chọn giữa hai góc nhìn hợp lý, không kích động",
    "experience_share": "mời người đọc chia sẻ trải nghiệm liên quan",
    "save_for_later": "nếu nội dung có giá trị tham khảo, gợi ý lưu lại một cách tự nhiên",
    "no_cta": "không ép tương tác và không thêm CTA ở cuối",
}


@dataclass(frozen=True)
class StyleBundle:
    hook_type: str
    style_type: str
    cta_type: str
    mode: str


def _fallback_stat(dimension: str, value: str, weight: float) -> StrategyStat:
    return StrategyStat(
        dimension=dimension,
        value=value,
        sample_count=0,
        weighted_score_14d=50.0,
        recent_score_7d=50.0,
        success_rate=0.0,
        current_weight=weight,
        last_used_at=None,
        status="insufficient_data",
        cooldown_until=None,
        retest_after=None,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def _complete_stats(values: list[str], stats: list[StrategyStat], dimension: str) -> list[StrategyStat]:
    if not values:
        raise ValueError(f"no active style values for {dimension}")
    by_value = {stat.value: stat for stat in stats if stat.value in values}
    default_weight = 1.0 / len(values)
    return [
        by_value.get(value) or _fallback_stat(dimension, value, default_weight)
        for value in values
    ]


def _pick_value(mode: str, values: list[str], stats: list[StrategyStat], rng) -> str:
    if mode == "explore":
        probabilities = selection.exploration_probabilities(stats, values)
        if probabilities:
            return selection.weighted_choice(list(probabilities.items()), rng)

    probabilities = selection.exploit_probabilities(stats)
    if not probabilities:
        equal = 1.0 / len(values)
        probabilities = {value: equal for value in values}
    return selection.weighted_choice(list(probabilities.items()), rng)


def choose_style_bundle(execute_fn, *, rng=None) -> StyleBundle:
    """Choose one hook/tone/CTA bundle using a shared 80/20 mode decision."""
    generator = rng or random.Random()
    style_registry.ensure_seed_styles(execute_fn)
    config = strategy_repository.load_config(execute_fn)
    exploration_rate = config.exploration_rate if config.adaptive_enabled else 0.0
    mode = selection.select_mode(generator, exploration_rate)

    chosen = {}
    for registry_dimension, strategy_dimension in _DIMENSIONS:
        variants = style_registry.list_active_styles(execute_fn, registry_dimension)
        values = [variant.value for variant in variants]
        stats = strategy_repository.load_stats(execute_fn, strategy_dimension)
        completed = _complete_stats(values, stats, strategy_dimension)
        chosen[strategy_dimension] = _pick_value(mode, values, completed, generator)

    return StyleBundle(
        hook_type=chosen["hook_type"],
        style_type=chosen["style_type"],
        cta_type=chosen["cta_type"],
        mode=mode,
    )


def style_instruction(bundle: StyleBundle) -> str:
    """Return a compact prompt instruction while preserving machine-readable style IDs."""
    hook = _HOOK_GUIDANCE.get(bundle.hook_type, bundle.hook_type)
    tone = _TONE_GUIDANCE.get(bundle.style_type, bundle.style_type)
    cta = _CTA_GUIDANCE.get(bundle.cta_type, bundle.cta_type)
    return (
        "\n\nYÊU CẦU PHONG CÁCH ADAPTIVE (không được làm sai dữ kiện):\n"
        f"- hook_type={bundle.hook_type}: {hook}.\n"
        f"- style_type={bundle.style_type}: {tone}.\n"
        f"- cta_type={bundle.cta_type}: {cta}.\n"
        "Giữ đúng yêu cầu độ dài, nguồn, hashtag và định dạng của nhiệm vụ gốc."
    )
