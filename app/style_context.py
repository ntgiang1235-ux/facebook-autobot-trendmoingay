from contextlib import contextmanager
from contextvars import ContextVar

from app.style_strategy import StyleBundle, style_instruction


_CURRENT: ContextVar[StyleBundle | None] = ContextVar("style_bundle", default=None)


def current_style_bundle() -> StyleBundle | None:
    return _CURRENT.get()


@contextmanager
def use_style_bundle(bundle: StyleBundle):
    token = _CURRENT.set(bundle)
    try:
        yield bundle
    finally:
        _CURRENT.reset(token)


def adaptive_prompt(prompt: str) -> str:
    """Append the active style instruction only inside an adaptive publish context."""
    bundle = current_style_bundle()
    if bundle is None:
        return prompt
    return f"{prompt}{style_instruction(bundle)}"
