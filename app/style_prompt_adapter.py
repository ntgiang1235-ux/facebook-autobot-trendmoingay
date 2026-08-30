from collections.abc import Callable

from app.style_context import adaptive_prompt, use_style_bundle
from app.style_strategy import StyleBundle


PRIMARY_GEMINI_CALL_INDEX = {
    "post": 1,
    "finance": 1,
    "philosophy": 1,
    "fun": 1,
    "recipe": 2,
}


def run_with_style(
    action: str,
    module: object,
    job_fn: Callable[[], object],
    bundle: StyleBundle,
):
    """Run one adaptive job while styling only its primary content-generation call.

    Legacy jobs use Gemini for auxiliary tasks too (for example recipe dish
    selection and seed comments). Decorating only the known primary call keeps
    the adaptive style strategy from changing those operational/helper prompts.
    The module function and style context are restored even when the job fails.
    """
    if action not in PRIMARY_GEMINI_CALL_INDEX:
        raise ValueError(f"unsupported adaptive style action: {action}")

    original_gemini = module.call_gemini
    target_index = PRIMARY_GEMINI_CALL_INDEX[action]
    call_index = 0

    def styled_gemini(prompt, timeout=30):
        nonlocal call_index
        call_index += 1
        outbound = adaptive_prompt(prompt) if call_index == target_index else prompt
        return original_gemini(outbound, timeout=timeout)

    with use_style_bundle(bundle):
        module.call_gemini = styled_gemini
        try:
            return job_fn()
        finally:
            module.call_gemini = original_gemini
