import os
import random
import sys
import urllib.parse

import autobot

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "").strip()


def search_pexels_image(query):
    """Tìm ảnh qua Pexels API. Trả về metadata ảnh hoặc None."""
    if not PEXELS_API_KEY:
        return None

    try:
        res = autobot.http.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "per_page": 15, "orientation": "square"},
            timeout=20,
        )
        res.raise_for_status()
        photos = res.json().get("photos", [])
        if not photos:
            return None

        random.shuffle(photos)
        photo = photos[0]
        src = photo.get("src", {})
        image_url = src.get("large") or src.get("large2x") or src.get("original")
        if not image_url:
            return None

        photographer = photo.get("photographer", "Pexels contributor")
        photo_page = photo.get("url", "https://www.pexels.com")
        attribution = f"📷 Ảnh: {photographer} / Pexels\n{photo_page}"
        return {
            "url": image_url,
            "source": "pexels",
            "attribution": attribution,
        }
    except Exception as e:
        print(f"⚠️ Pexels lỗi: {e}")
        return None


def search_bing_image(query):
    """Fallback tạm thời nếu chưa có Pexels key hoặc Pexels không trả ảnh."""
    try:
        from bs4 import BeautifulSoup
        import json

        encoded = urllib.parse.quote(query)
        url = f"https://www.bing.com/images/search?q={encoded}"
        res = autobot.http.get(url, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        images = soup.find_all("a", class_="iusc")
        random.shuffle(images)

        for item in images[:10]:
            raw = item.get("m")
            if not raw:
                continue
            data = json.loads(raw)
            image_url = data.get("murl")
            if image_url and image_url.startswith("http"):
                return {
                    "url": image_url,
                    "source": "bing",
                    "attribution": "",
                }
    except Exception as e:
        print(f"⚠️ Bing fallback lỗi: {e}")
    return None


def find_image(query):
    image = search_pexels_image(query)
    if image:
        print("✅ Đã tìm ảnh từ Pexels.")
        return image

    if not PEXELS_API_KEY:
        print("ℹ️ Chưa có PEXELS_API_KEY, tạm dùng Bing fallback.")
    else:
        print("ℹ️ Pexels không có ảnh phù hợp, tạm dùng Bing fallback.")
    return search_bing_image(query)


def download_image(image_url, prefix):
    """Tải ảnh về runner rồi upload file binary lên Facebook."""
    res = autobot.http.get(image_url, timeout=30, stream=True)
    res.raise_for_status()

    content_type = res.headers.get("Content-Type", "").lower()
    if not content_type.startswith("image/"):
        raise ValueError(f"URL không trả về ảnh hợp lệ: {content_type}")

    ext = ".jpg"
    if "png" in content_type:
        ext = ".png"
    elif "webp" in content_type:
        ext = ".webp"

    path = f"{prefix}{ext}"
    with open(path, "wb") as f:
        for chunk in res.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    return path


def publish_photo_or_text(status_text, image, prefix):
    """Ưu tiên ảnh; nếu ảnh không dùng được thì đăng text-only."""
    temp_path = None
    final_text = status_text
    image_url = image.get("url") if image else None
    attribution = image.get("attribution", "") if image else ""

    if attribution:
        final_text = f"{status_text}\n\n{attribution}"

    try:
        if image_url:
            temp_path = download_image(image_url, prefix)
            with open(temp_path, "rb") as f:
                code, data = autobot.call_fb_api(
                    "me/photos",
                    {"message": final_text},
                    files={"source": f},
                )
            if code == 200:
                return code, data
            print(f"⚠️ Facebook từ chối ảnh: {data}. Chuyển sang text-only.")

        return autobot.call_fb_api("me/feed", {"message": status_text})
    except Exception as e:
        print(f"⚠️ Không thể xử lý ảnh: {e}. Chuyển sang text-only.")
        return autobot.call_fb_api("me/feed", {"message": status_text})
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def fun_job():
    print("🤡 Đang chạy Job: Giải trí & Tấu hài...")

    topics = [
        "nỗi khổ đi làm, deadline dí ngập đầu, sếp hối",
        "tình trạng rỗng ví, chưa hết tháng đã hết tiền",
        "tình yêu mập mờ, ế bằng thực lực, bị fomo tình cảm",
        "nỗi ám ảnh cân nặng, miệng nói giảm cân nhưng tay cầm ly trà sữa",
        "thức khuya lướt điện thoại, sáng dậy không nổi",
        "những triết lý vô tri, ngớ ngẩn nhưng nghe cực kỳ hợp lý",
    ]
    tones = [
        "xéo xắt, châm biếm sâu cay",
        "than vãn một cách đáng yêu, tự dìm hàng bản thân",
        "thả thính dạo nhưng cực kỳ củ chuối",
        "vô tri, lầy lội, đọc xong thấy tốn thời gian nhưng vẫn buồn cười",
        "tỏ ra trưởng thành nhưng chốt câu lại cực kỳ trẻ trâu",
    ]

    topic = random.choice(topics)
    tone = random.choice(tones)
    print(f"🎯 Chủ đề: {topic}")

    prompt = (
        f"Bạn là admin Gen Z của kênh TREND MỖI NGÀY. "
        f"Hãy viết 1 status tấu hài dưới 40 chữ về chủ đề: '{topic}'.\n"
        f"Văn phong bắt buộc: {tone}.\n"
        "KHÔNG dùng ngoặc kép, KHÔNG giải thích. "
        "Kèm #TRENDMOINGAY #GiaiTri và vài icon phù hợp."
    )
    status_text = autobot.call_gemini(prompt)
    if not status_text:
        raise RuntimeError("Gemini không tạo được nội dung fun")

    image_queries = {
        "nỗi khổ đi làm, deadline dí ngập đầu, sếp hối": "funny stressed office worker",
        "tình trạng rỗng ví, chưa hết tháng đã hết tiền": "funny empty wallet money",
        "tình yêu mập mờ, ế bằng thực lực, bị fomo tình cảm": "funny lonely love reaction",
        "nỗi ám ảnh cân nặng, miệng nói giảm cân nhưng tay cầm ly trà sữa": "funny diet food temptation",
        "thức khuya lướt điện thoại, sáng dậy không nổi": "funny sleepy phone night",
        "những triết lý vô tri, ngớ ngẩn nhưng nghe cực kỳ hợp lý": "funny confused cat thinking",
    }
    image = find_image(image_queries.get(topic, "funny reaction"))
    code, data = publish_photo_or_text(status_text, image, "fun_post")

    if code != 200:
        raise RuntimeError(f"Lỗi đăng bài giải trí: {data}")

    print("✅ Đã đăng bài giải trí thành công!")
    autobot.send_tele("📣 <b>CHUYÊN MỤC TẤU HÀI ĐÃ LÊN SÓNG!</b>")


def recipe_job():
    print("🍳 Đang chạy Job: Chuyên mục ẩm thực & Affiliate...")

    dish_prompt = (
        "Hãy gợi ý ngẫu nhiên 1 món ăn tối gia đình Việt Nam thật hấp dẫn. "
        "CHỈ trả về đúng tên món ăn, không giải thích, không dùng dấu câu."
    )
    dish = autobot.call_gemini(dish_prompt) or "Thịt ba chỉ rang cháy cạnh"
    print(f"💡 Món hôm nay: {dish}")

    prompt = (
        "Bạn là admin sành ăn của kênh TREND MỖI NGÀY. "
        f"Viết bài Facebook chuyên mục 'Chiều nay ăn gì?' với món '{dish}'.\n"
        "Văn phong gần gũi, kích thích vị giác; chia sẻ 3 bước nấu nhanh. "
        "Thêm #TRENDMOINGAY #ChieuNayAnGi #ThucDonGiaDinh. "
        "Không chào hỏi; trình bày bằng icon phù hợp; cuối bài mời xem đồ nghề bếp ở bình luận."
    )
    status_text = autobot.call_gemini(prompt)
    if not status_text:
        raise RuntimeError("Gemini không tạo được nội dung recipe")

    image = find_image(f"Vietnamese food {dish}")
    code, data = publish_photo_or_text(status_text, image, "recipe_post")
    post_id = data.get("post_id") or data.get("id")

    if code != 200 or not post_id:
        raise RuntimeError(f"Lỗi đăng bài ẩm thực: {data}")

    print(f"✅ Đã đăng gợi ý món '{dish}' thành công!")
    autobot.send_tele(f"📣 <b>CHUYÊN MỤC ẨM THỰC LÊN SÓNG:</b>\n{dish}")

    seed_prompt = (
        f"Viết 1 bình luận mồi dưới 20 chữ cho món '{dish}', tự nhiên, dân dã, có 1 icon. "
        "Không giải thích, không dùng ngoặc kép."
    )
    seed_msg = autobot.call_gemini(seed_prompt) or f"Món {dish} nhìn là muốn ăn ngay luôn đó! 🤤"
    affiliate_link = os.getenv("AFFILIATE_LINK", "").strip()
    comment = seed_msg + (f"\n\n🔗 {affiliate_link}" if affiliate_link else "")

    c_code, c_data = autobot.call_fb_api(
        f"{post_id}/comments",
        {"message": comment},
    )
    if c_code == 200:
        print("✅ Đã đăng bình luận mồi thành công!")
    else:
        print(f"⚠️ Bài đã đăng nhưng bình luận mồi lỗi: {c_data}")


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Vui lòng truyền action")

    action = sys.argv[1]
    autobot.validate_runtime_config(action)

    if action == "fun":
        fun_job()
    elif action == "recipe":
        recipe_job()
    elif action == "post":
        autobot.single_post_job()
    elif action == "summary":
        autobot.daily_summary_job()
    elif action == "finance":
        autobot.financial_post_job()
    elif action == "philosophy":
        autobot.philosophy_post_job()
    elif action == "reply":
        autobot.auto_reply_job()
    elif action == "veo":
        autobot.veo_prompt_job()
    else:
        raise SystemExit(f"Action không hợp lệ: {action}")


if __name__ == "__main__":
    main()
