from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class PublicationContext:
    run_key: str
    category: str
    scheduled_for: str | None
    strategy_mode: str
    strategy_version: int | None


_CURRENT: ContextVar[PublicationContext | None] = ContextVar(
    "publication_context", default=None
)


def current_publication_context() -> PublicationContext | None:
    return _CURRENT.get()


@contextmanager
def use_publication_context(context: PublicationContext):
    token = _CURRENT.set(context)
    try:
        yield context
    finally:
        _CURRENT.reset(token)
