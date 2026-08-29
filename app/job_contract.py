from dataclasses import dataclass
from typing import Callable, Optional


VALID_STATUSES = {"success", "skipped", "failed"}


@dataclass(frozen=True)
class JobOutcome:
    status: str
    detail: str = ""

    def __post_init__(self):
        if self.status not in VALID_STATUSES:
            raise ValueError(f"Invalid job status: {self.status}")


def success(detail: str = "") -> JobOutcome:
    return JobOutcome("success", detail)


def skipped(detail: str) -> JobOutcome:
    return JobOutcome("skipped", detail)


def _best_effort(callback: Callable, *args) -> None:
    try:
        callback(*args)
    except Exception as exc:
        print(f"⚠️ Observability callback lỗi: {exc}")


def run_job(
    action: str,
    job_fn: Callable[[], Optional[JobOutcome]],
    recorder: Callable[[str, str], None],
    notifier: Callable[[str, Exception], None],
) -> JobOutcome:
    """Run one job with an explicit success/skipped/failed contract.

    Operational failures are always re-raised so GitHub Actions receives a
    non-zero exit code. Recorder/notifier failures never hide the original
    business failure.
    """
    try:
        result = job_fn()
    except Exception as exc:
        detail = str(exc)
        _best_effort(recorder, "failed", detail)
        _best_effort(notifier, action, exc)
        raise

    outcome = result if isinstance(result, JobOutcome) else success()
    _best_effort(recorder, outcome.status, outcome.detail)
    return outcome
