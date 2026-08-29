import os
from typing import Callable


HEALTH_PROMPT = "Trả lời đúng một từ: OK"


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing {name}")
    return value


def run_health_check(http, gemini_call: Callable, db_execute: Callable) -> list[str]:
    """Check critical external dependencies and raise one aggregated error on failure."""
    healthy: list[str] = []
    failures: list[str] = []

    try:
        rows = db_execute("SELECT 1")
        if not rows:
            raise RuntimeError("empty response")
        healthy.append("turso")
    except Exception as exc:
        failures.append(f"turso: {exc}")

    try:
        token = _required_env("FB_ACCESS_TOKEN")
        response = http.get(
            "https://graph.facebook.com/v25.0/me",
            params={"access_token": token, "fields": "id"},
            timeout=20,
        )
        response.raise_for_status()
        if not response.json().get("id"):
            raise RuntimeError("missing page id")
        healthy.append("facebook")
    except Exception as exc:
        failures.append(f"facebook: {exc}")

    try:
        result = gemini_call(HEALTH_PROMPT, timeout=20)
        if not result:
            raise RuntimeError("empty response")
        healthy.append("gemini")
    except Exception as exc:
        failures.append(f"gemini: {exc}")

    try:
        api_key = _required_env("PEXELS_API_KEY")
        response = http.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": api_key},
            params={"query": "nature", "per_page": 1},
            timeout=20,
        )
        response.raise_for_status()
        healthy.append("pexels")
    except Exception as exc:
        failures.append(f"pexels: {exc}")

    try:
        telegram_token = _required_env("TELEGRAM_TOKEN")
        _required_env("TELEGRAM_CHAT_ID")
        response = http.get(
            f"https://api.telegram.org/bot{telegram_token}/getMe",
            timeout=20,
        )
        response.raise_for_status()
        if not response.json().get("ok"):
            raise RuntimeError("getMe returned ok=false")
        healthy.append("telegram")
    except Exception as exc:
        failures.append(f"telegram: {exc}")

    if failures:
        raise RuntimeError("Health check failed: " + "; ".join(failures))
    return healthy
