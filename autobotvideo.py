import concurrent.futures
import os
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import libsql
import requests
from dotenv import load_dotenv
from google import genai

load_dotenv()

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "").strip()
GEMINI_API_KEYS = [k.strip() for k in os.getenv("GEMINI_API_KEYS", "").split(",") if k.strip()]
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL", "").strip()
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "").strip()

MIN_DURATION = 5
MAX_DURATION = 90
MAX_VIDEO_BYTES = 120 * 1024 * 1024
TEMP_VIDEO = Path("video_upload.mp4")

http = requests.Session()
http.headers.update({"User-Agent": "TRENDMOINGAY-AutoBot/1.0"})

VIDEO_TOPICS = [
    "funny cats",
    "funny dogs",
    "office lifestyle",
    "Vietnamese food",
    "street food",
    "beautiful nature",
    "travel asia",
    "technology lifestyle",
    "fitness lifestyle",
    "coffee lifestyle",
]


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")


def validate_config(skip_fb: bool = False) -> None:
    missing = []
    for key, value in (
        ("PEXELS_API_KEY", PEXELS_API_KEY),
        ("GEMINI_API_KEYS", GEMINI_API_KEYS),
        ("TURSO_DATABASE_URL", TURSO_DATABASE_URL),
        ("TURSO_AUTH_TOKEN", TURSO_AUTH_TOKEN),
    ):
        if not value:
            missing.append(key)
    if not skip_fb and not FB_ACCESS_TOKEN:
        missing.append("FB_ACCESS_TOKEN")
    if missing:
        raise RuntimeError("Thiếu biến môi trường: " + ", ".join(missing))


def db_execute(query: str, params: tuple = ()) -> list[tuple]:
    conn = libsql.connect(database=TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN)
    try:
        cur = conn.execute(query, params)
        conn.commit()
        return cur.fetchall()
    finally:
        conn.close()


def init_db() -> None:
    db_execute(
        """
        CREATE TABLE IF NOT EXISTS posted_videos (
            source_id TEXT PRIMARY KEY,
            source_url TEXT NOT NULL,
            topic TEXT,
            creator TEXT,
            posted_at TEXT NOT NULL
        )
        """
    )


def is_posted(source_id: str) -> bool:
    return bool(db_execute("SELECT source_id FROM posted_videos WHERE source_id = ?", (source_id,)))


def save_posted(source_id: str, source_url: str, topic: str, creator: str) -> None:
    db_execute(
        "INSERT OR IGNORE INTO posted_videos VALUES (?, ?, ?, ?, ?)",
        (source_id, source_url, topic, creator, datetime.now().isoformat()),
    )


def send_telegram(message: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        http.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=15,
        )
    except Exception as exc:
        log(f"⚠️ Telegram lỗi: {exc}")


def _gemini_worker(client: genai.Client, prompt: str) -> str:
    response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    return (response.text or "").strip()


def call_gemini(prompt: str, timeout: int = 30) -> Optional[str]:
    keys = GEMINI_API_KEYS.copy()
    random.shuffle(keys)
    for key in keys:
        try:
            client = genai.Client(api_key=key)
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(_gemini_worker, client, prompt).result(timeout=timeout)
        except Exception as exc:
            log(f"⚠️ Gemini key lỗi: {exc}")
    return None


def search_pexels(topic: str) -> list[dict]:
    response = http.get(
        "https://api.pexels.com/v1/videos/search",
        headers={"Authorization": PEXELS_API_KEY},
        params={
            "query": topic,
            "orientation": "portrait",
            "size": "small",
            "locale": "vi-VN",
            "per_page": 30,
            "page": random.randint(1, 5),
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("videos", [])


def choose_video_file(video: dict) -> Optional[dict]:
    candidates = [
        f
        for f in video.get("video_files", [])
        if f.get("file_type") == "video/mp4"
        and f.get("link")
        and (f.get("height") or 0) >= (f.get("width") or 0)
        and (f.get("height") or 0) <= 1920
    ]
    if not candidates:
        return None

    def score(item: dict) -> tuple[int, int]:
        height = item.get("height") or 0
        preferred = 1 if 720 <= height <= 1280 else 0
        return preferred, height

    return max(candidates, key=score)


def pick_unposted_video() -> tuple[dict, dict, str]:
    topics = VIDEO_TOPICS.copy()
    random.shuffle(topics)

    for topic in topics:
        log(f"🔍 Tìm Pexels video: {topic}")
        try:
            videos = search_pexels(topic)
        except Exception as exc:
            log(f"⚠️ Pexels search lỗi: {exc}")
            continue

        random.shuffle(videos)
        for video in videos:
            source_id = str(video.get("id") or "")
            duration = int(video.get("duration") or 0)
            if not source_id or duration < MIN_DURATION or duration > MAX_DURATION:
                continue
            if is_posted(source_id):
                continue

            file_info = choose_video_file(video)
            if file_info:
                return video, file_info, topic

    raise RuntimeError("Không tìm thấy Pexels video mới phù hợp")


def download_video(url: str, target: Path) -> None:
    if target.exists():
        target.unlink()

    with http.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        content_length = int(response.headers.get("Content-Length", "0") or 0)
        if content_length and content_length > MAX_VIDEO_BYTES:
            raise RuntimeError(f"Video quá lớn: {content_length / 1024 / 1024:.1f} MB")

        downloaded = 0
        with target.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                downloaded += len(chunk)
                if downloaded > MAX_VIDEO_BYTES:
                    raise RuntimeError("Video vượt giới hạn 120 MB")
                f.write(chunk)

    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError("File video tải về rỗng")


def generate_caption(topic: str, creator: str, pexels_url: str) -> str:
    prompt = (
        "Bạn là admin trẻ trung của Fanpage TREND MỖI NGÀY. "
        f"Viết đúng 1 caption Facebook Reels bằng tiếng Việt cho video chủ đề '{topic}'. "
        "Dưới 55 chữ, tự nhiên, tích cực, dễ tương tác, không bịa sự kiện hay nhân vật cụ thể. "
        "Không giải thích, không ngoặc kép. Thêm #TRENDMOINGAY #Reels và 2 hashtag phù hợp."
    )
    caption = call_gemini(prompt) or "Một khoảnh khắc đáng xem hôm nay ✨ #TRENDMOINGAY #Reels"
    attribution = f"\n\n🎥 Video: {creator or 'Pexels Creator'} / Pexels\n{pexels_url}"
    return caption.strip() + attribution


def upload_facebook(video_path: Path, caption: str) -> dict:
    url = "https://graph.facebook.com/v25.0/me/videos"
    with video_path.open("rb") as f:
        response = http.post(
            url,
            data={"access_token": FB_ACCESS_TOKEN, "description": caption},
            files={"source": f},
            timeout=600,
        )
    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text[:1000]}
    if response.status_code != 200 or not data.get("id"):
        raise RuntimeError(f"Facebook upload lỗi HTTP {response.status_code}: {data}")
    return data


def video_post_job(
    dry_run: bool = False,
    on_published: Callable[..., object] | None = None,
) -> None:
    validate_config(skip_fb=dry_run)
    init_db()
    log("🎬 Bắt đầu job video Pexels" + (" [DRY-RUN]" if dry_run else ""))

    video, file_info, topic = pick_unposted_video()
    source_id = str(video["id"])
    source_url = video.get("url") or f"https://www.pexels.com/video/{source_id}/"
    creator = (video.get("user") or {}).get("name") or "Pexels Creator"
    duration = video.get("duration")

    try:
        log(f"🎯 Chọn video {source_id} | {topic} | {duration}s | {creator}")
        download_video(file_info["link"], TEMP_VIDEO)
        log(f"📥 Đã tải video: {TEMP_VIDEO.stat().st_size / 1024 / 1024:.1f} MB")

        caption = generate_caption(topic, creator, source_url)
        log(f"📝 Caption: {caption.splitlines()[0]}")

        if dry_run:
            log("🔒 Dry-run: không upload Facebook và không đánh dấu đã đăng")
            return

        data = upload_facebook(TEMP_VIDEO, caption)
        if on_published is not None:
            on_published(
                endpoint="me/videos",
                request_data={"message": caption},
                response=data,
                topic_text=topic,
                source_url=source_url,
                format_type="video",
            )
        save_posted(source_id, source_url, topic, creator)
        log(f"✅ Đăng video thành công! Facebook ID: {data['id']}")
        send_telegram(
            f"📣 <b>VIDEO ĐÃ LÊN SÓNG</b>\n"
            f"Pexels ID: {source_id}\n"
            f"Chủ đề: {topic}\n"
            f"Creator: {creator}"
        )
    finally:
        if TEMP_VIDEO.exists():
            TEMP_VIDEO.unlink()
            log("🧹 Đã xóa file video tạm")


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "help"
    if action == "run":
        raise SystemExit(
            "Production video phải chạy qua: python hardening_runner.py video"
        )
    if action == "dry-run":
        video_post_job(dry_run=True)
        return
    print("Cách dùng: python autobotvideo.py dry-run")


if __name__ == "__main__":
    main()
