import os
import random
import re
import unicodedata

import autobot

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "").strip()


def _photo_to_image(photo):
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
        "alt": photo.get("alt", ""),
    }


def search_pexels_image(query):
    """Tìm ngẫu nhiên một ảnh Pexels từ tập kết quả phù hợp."""
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
        for photo in photos:
            image = _photo_to_image(photo)
            if image:
                return image
        return None
    except Exception as e:
        print(f"⚠️ Pexels lỗi: {e}")
        return None


def find_image(query):
    """Chỉ dùng nguồn ảnh Pexels; không có ảnh thì caller sẽ đăng text-only."""
    image = search_pexels_image(query)
    if image:
        print("✅ Đã tìm ảnh từ Pexels.")
        return image

    if not PEXELS_API_KEY:
        print("ℹ️ Chưa có PEXELS_API_KEY; sẽ đăng text-only.")
    else:
        print("ℹ️ Pexels không có ảnh phù hợp; sẽ đăng text-only.")
    return None


def _normalize_words(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return [w for w in re.findall(r"[a-z0-9]+", text) if len(w) >= 3]


def recipe_image_relevance_score(query, alt_text):
    """Điểm khớp đơn giản giữa truy vấn món ăn và mô tả ảnh Pexels."""
    query_words = set(_normalize_words(query))
    alt_words = set(_normalize_words(alt_text))
    if not query_words or not alt_words:
        return 0

    generic = {
        "food", "dish", "meal", "dinner", "lunch", "recipe", "cuisine",
        "plate", "table", "fresh", "delicious", "with", "and", "the",
    }
    meaningful = query_words - generic
    if not meaningful:
        meaningful = query_words
    return len(meaningful & alt_words)


def build_recipe_fallback_queries(dish):
    """Fallback rộng, không giới hạn vào món Việt Nam."""
    return [
        f"{dish} food",
        f"{dish} dinner",
        "international food dinner meal",
        "homemade dinner food",
        "delicious family meal",
    ]


def generate_recipe_search_query(dish):
    """Nhờ Gemini chuyển tên món thành truy vấn ảnh tiếng Anh rõ ràng."""
    prompt = (
        f"Món ăn là: {dish}. Hãy tạo đúng 1 cụm từ tìm ảnh bằng tiếng Anh, tối đa 8 từ, "
        "nêu đúng tên món và loại ẩm thực nếu biết. Chỉ trả về cụm từ tìm kiếm, không giải thích."
    )
    result = autobot.call_gemini(prompt)
    return (result or dish).strip()


def search_relevant_recipe_image(dish):
    """Ưu tiên ảnh khớp món; nếu không đủ liên quan sẽ trả None để fallback rộng."""
    if not PEXELS_API_KEY:
        return None

    english_query = generate_recipe_search_query(dish)
    queries = []
    for q in [english_query, dish, f"{english_query} food"]:
        q = (q or "").strip()
        if q and q not in queries:
            queries.append(q)

    best_image = None
    best_score = -1

    for query in queries:
        try:
            res = autobot.http.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": PEXELS_API_KEY},
                params={"query": query, "per_page": 20, "orientation": "square"},
                timeout=20,
            )
            res.raise_for_status()
            photos = res.json().get("photos", [])
        except Exception as e:
            print(f"⚠️ Pexels recipe search lỗi với '{query}': {e}")
            continue

        # Pexels đã xếp theo relevance; không shuffle ở nhánh chính xác.
        for index, photo in enumerate(photos[:10]):
            image = _photo_to_image(photo)
            if not image:
                continue
            score = recipe_image_relevance_score(query, photo.get("alt", ""))
            # Kết quả đầu tiên của Pexels được cộng nhẹ vì API đã rank theo relevance.
            if index == 0:
                score += 1
            if score > best_score:
                best_score = score
                best_image = image

        if best_score >= 2:
            print(f"✅ Ảnh recipe khớp món (score={best_score}) với truy vấn: {query}")
            return best_image

    if best_image and best_score >= 1:
        print(f"✅ Dùng ảnh recipe gần đúng nhất (score={best_score}).")
        return best_image

    print("ℹ️ Không tìm được ảnh đủ liên quan trực tiếp tới món; chuyển sang fallback Pexels rộng.")
    return None


def find_recipe_image(dish):
    exact = search_relevant_recipe_image(dish)
    if exact:
        return exact

    fallback_queries = build_recipe_fallback_queries(dish)
    random.shuffle(fallback_queries)
    for query in fallback_queries:
        image = find_image(query)
        if image:
            print(f"ℹ️ Recipe đang dùng ảnh Pexels fallback rộng: {query}")
            return image
    return None


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
        "Hãy gợi ý ngẫu nhiên đúng 1 món ăn hấp dẫn cho bữa chính hoặc bữa tối, có thể thuộc bất kỳ nền ẩm thực nào trên thế giới. "
        "Luân phiên đa dạng giữa Việt Nam, Thái Lan, Nhật Bản, Hàn Quốc, Trung Quốc, Ấn Độ, Ý, Pháp, Mexico, Mỹ, "
        "Địa Trung Hải, Trung Đông và các nền ẩm thực phổ biến khác. Ưu tiên món có nguyên liệu dễ tìm và có thể nấu tại nhà. "
        "LỆNH BẮT BUỘC: CHỈ trả về đúng tên món ăn bằng tên phổ biến nhất, tuyệt đối không giải thích, không dùng dấu câu ở cuối."
    )
    dish = autobot.call_gemini(dish_prompt) or "Spaghetti Carbonara"
    print(f"💡 Món hôm nay: {dish}")

    prompt = (
        "Bạn là admin sành ăn của kênh TREND MỖI NGÀY. "
        f"Viết bài Facebook chuyên mục 'Chiều nay ăn gì?' với món '{dish}'.\n"
        "Món có thể thuộc bất kỳ nền ẩm thực nào. Hãy giới thiệu ngắn nguồn gốc/phong cách ẩm thực nếu phù hợp, "
        "văn phong gần gũi, kích thích vị giác; chia sẻ 3 bước nấu thực tế, ngắn gọn và dễ làm tại nhà. "
        "Thêm #TRENDMOINGAY #ChieuNayAnGi #MonNgonMoiNgay và 1 hashtag liên quan nền ẩm thực của món. "
        "Không chào hỏi; trình bày bằng icon phù hợp; cuối bài mời xem đồ nghề bếp ở bình luận."
    )
    status_text = autobot.call_gemini(prompt)
    if not status_text:
        raise RuntimeError("Gemini không tạo được nội dung recipe")

    image = find_recipe_image(dish)
    code, data = publish_photo_or_text(status_text, image, "recipe_post")
    post_id = data.get("post_id") or data.get("id")

    if code != 200 or not post_id:
        raise RuntimeError(f"Lỗi đăng bài ẩm thực: {data}")

    print(f"✅ Đã đăng gợi ý món '{dish}' thành công!")
    autobot.send_tele(f"📣 <b>CHUYÊN MỤC ẨM THỰC LÊN SÓNG:</b>\n{dish}")

    seed_prompt = (
        f"Viết 1 bình luận mồi dưới 20 chữ cho món '{dish}', tự nhiên, vui vẻ, có 1 icon. "
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
    raise SystemExit(
        "runner.py là module legacy. Hãy chạy: python hardening_runner.py <action>"
    )


if __name__ == "__main__":
    main()
