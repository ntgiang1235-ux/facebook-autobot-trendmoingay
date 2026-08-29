import os

import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

http = requests.Session()


def send_message(message: str) -> bool:
    """Send a Telegram message without ever raising into the job path."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        response = http.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
            },
            timeout=15,
        )
        response.raise_for_status()
        return True
    except Exception as exc:
        print(f"⚠️ Telegram notification lỗi: {exc}")
        return False


def send_failure(action: str, error: Exception, run_url: str | None = None) -> bool:
    lines = [
        "🚨 AUTOBOT FAILED",
        "",
        f"Job: {action}",
        f"Error: {str(error)[:500]}",
    ]
    if run_url:
        lines.extend(["", f"GitHub Run: {run_url}"])
    return send_message("\n".join(lines))
