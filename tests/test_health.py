import os
import unittest
from unittest.mock import patch

from app.health import run_health_check


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self.payload = payload or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, failures=None):
        self.failures = failures or set()
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "facebook.com" in url:
            if "facebook" in self.failures:
                return FakeResponse(status_code=503)
            return FakeResponse({"id": "page-1"})
        if "pexels.com" in url:
            if "pexels" in self.failures:
                return FakeResponse(status_code=503)
            return FakeResponse({"photos": []})
        if "api.telegram.org" in url:
            if "telegram" in self.failures:
                return FakeResponse(status_code=503)
            return FakeResponse({"ok": True})
        raise AssertionError(f"Unexpected URL: {url}")


ENV = {
    "FB_ACCESS_TOKEN": "fb-token",
    "PEXELS_API_KEY": "pexels-key",
    "TELEGRAM_TOKEN": "telegram-token",
    "TELEGRAM_CHAT_ID": "chat-id",
}


class HealthTests(unittest.TestCase):
    def test_all_dependencies_healthy(self):
        session = FakeSession()
        with patch.dict(os.environ, ENV, clear=False):
            result = run_health_check(
                session,
                gemini_call=lambda prompt, timeout=20: "OK",
                db_execute=lambda query, params=(): [(1,)],
            )

        self.assertEqual(set(result), {"turso", "facebook", "gemini", "pexels", "telegram"})

    def test_dependency_failure_is_aggregated(self):
        session = FakeSession({"facebook"})
        with patch.dict(os.environ, ENV, clear=False):
            with self.assertRaisesRegex(RuntimeError, "facebook"):
                run_health_check(
                    session,
                    gemini_call=lambda prompt, timeout=20: "OK",
                    db_execute=lambda query, params=(): [(1,)],
                )


if __name__ == "__main__":
    unittest.main()
