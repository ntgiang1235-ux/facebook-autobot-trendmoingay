import time

TRANSIENT_MARKERS = (
    "status=502",
    "502 bad gateway",
    "status=503",
    "503 service unavailable",
    "status=504",
    "504 gateway timeout",
    "connect to upstream failed",
    "connection reset",
    "connection refused",
    "timed out",
    "timeout",
    "temporarily unavailable",
)


def is_transient_turso_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in TRANSIENT_MARKERS)


def run_with_retry(operation, *, sleep_fn=time.sleep, delays=(2, 5, 10)):
    attempts = len(delays) + 1
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as exc:
            if not is_transient_turso_error(exc) or attempt >= len(delays):
                raise
            delay = delays[attempt]
            print(f"⚠️ Turso tạm lỗi ({exc}). Thử lại sau {delay}s... [{attempt + 2}/{attempts}]")
            sleep_fn(delay)
