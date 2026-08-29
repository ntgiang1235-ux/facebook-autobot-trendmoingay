import os
import time
import random
import requests
import xml.etree.ElementTree as ET
import libsql
import re
import sys
import concurrent.futures
import json
import urllib.parse
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from google import genai
from dotenv import load_dotenv
from bs4 import BeautifulSoup

# Khởi tạo môi trường
load_dotenv()

# ================= 1. CẤU HÌNH & KHỞI TẠO SESSION =================
GEMINI_API_KEYS = [k.strip() for k in os.getenv("GEMINI_API_KEYS", "").split(",") if k.strip()]
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN")
FB_PAGE_ID = os.getenv("FB_PAGE_ID")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
BLACKLIST_WORDS = [w.strip().lower() for w in os.getenv("BLACKLIST_WORDS", "").split(",") if w.strip()]
AFFILIATE_LINK = os.getenv("AFFILIATE_LINK", "").strip()

# Dùng Session để tăng tốc mạng, tái sử dụng TCP connection
http = requests.Session()
http.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})

# ================= 2. DATABASE TURSO =================
TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

def validate_database_config():
    missing = []
    if not TURSO_DATABASE_URL:
        missing.append("TURSO_DATABASE_URL")
    if not TURSO_AUTH_TOKEN:
        missing.append("TURSO_AUTH_TOKEN")
    if missing:
        raise RuntimeError(
            "Thiếu cấu hình Turso: " + ", ".join(missing)
            + ". Hãy thêm vào .env hoặc GitHub Actions Secrets."
        )

def execute_db(query, params=()):
    """Thực thi SQL trên Turso qua kết nối remote libSQL."""
    validate_database_config()
    conn = libsql.connect(
        database=TURSO_DATABASE_URL,
        auth_token=TURSO_AUTH_TOKEN,
    )
    try:
        cur = conn.execute(query, params)
        conn.commit()
        return cur.fetchall()
    finally:
        conn.close()

def init_db():
    execute_db('''CREATE TABLE IF NOT EXISTS posted_news
                  (link TEXT PRIMARY KEY, title TEXT, date TEXT)''')
    execute_db('''CREATE TABLE IF NOT EXISTS replied_comments
                  (comment_id TEXT PRIMARY KEY)''')
    try:
        # Xóa lịch sử bài cũ hơn 30 ngày. Không VACUUM trên database remote.
        execute_db("DELETE FROM posted_news WHERE date <= date('now', '-30 days')")
    except Exception as e:
        print(f"⚠️ Lỗi dọn dẹp Database: {e}")

def is_posted(link):
    res = execute_db("SELECT link FROM posted_news WHERE link=?", (link,))
    return len(res) > 0

def save_posted(link, title):
    today = datetime.now().strftime("%Y-%m-%d")
    execute_db("INSERT OR IGNORE INTO posted_news VALUES (?, ?, ?)", (link, title, today))

def is_replied(comment_id):
    res = execute_db("SELECT comment_id FROM replied_comments WHERE comment_id=?", (comment_id,))
    return len(res) > 0

def mark_replied(comment_id):
    execute_db("INSERT OR IGNORE INTO replied_comments VALUES (?)", (comment_id,))

def get_today_news_titles():
    today = datetime.now().strftime("%Y-%m-%d")
    return execute_db("SELECT title, link FROM posted_news WHERE date=?", (today,))

def validate_runtime_config(action):
    """Kiểm tra biến môi trường bắt buộc cho job sắp chạy."""
    missing = []
    if action in {"post", "finance", "philosophy", "reply", "recipe", "fun", "summary"} and not FB_ACCESS_TOKEN:
        missing.append("FB_ACCESS_TOKEN")
    if action in {"post", "summary", "finance", "philosophy", "reply", "veo", "recipe", "fun"} and not GEMINI_API_KEYS:
        missing.append("GEMINI_API_KEYS")
    if action == "summary" and not FB_PAGE_ID:
        missing.append("FB_PAGE_ID")
    if missing:
        raise RuntimeError("Thiếu biến môi trường: " + ", ".join(sorted(set(missing))))

# ================= 3. HELPERS (TELEGRAM & FB API & GEMINI) =================
def send_tele(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        http.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                  json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"⚠️ Tele lỗi: {e}")

def call_fb_api(endpoint, data, files=None):
    """Hàm xử lý chung cho mọi request đến Facebook Graph API"""
    url = f"https://graph.facebook.com/v25.0/{endpoint}"
    data['access_token'] = FB_ACCESS_TOKEN
    try:
        res = http.post(url, data=data, files=files, timeout=60)
        return res.status_code, res.json()
    except Exception as e:
        print(f"⚠️ Lỗi kết nối FB API: {e}")
        return 500, {}

def get_fb_api(endpoint, params=None):
    """Hàm GET dữ liệu từ Facebook (để đọc bình luận, lấy bài viết)"""
    if params is None: params = {}
    params['access_token'] = FB_ACCESS_TOKEN
    url = f"https://graph.facebook.com/v25.0/{endpoint}"
    try:
        res = http.get(url, params=params, timeout=30)
        return res.status_code, res.json()
    except Exception as e:
        print(f"⚠️ Lỗi GET FB API: {e}")
        return 500, {}

def _gemini_worker(client, prompt):
    return client.models.generate_content(model="gemini-2.5-flash", contents=prompt).text.strip()

def call_gemini(prompt, timeout=30):
    keys = GEMINI_API_KEYS.copy()
    random.shuffle(keys)
    for key in keys:
        try:
            client = genai.Client(api_key=key)
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_gemini_worker, client, prompt)
                return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            print(f"⏳  Gemini API Timeout ({timeout}s). Chuyển key...")
        except Exception as e:
            print(f"⚠️ Gemini Key lỗi: {e}")
    return None

# ================= 4. LOGIC CÀO TIN =================
def is_safe(text):
    if not text: return True
    text_lower = text.lower()
    return not any(bw in text_lower for bw in BLACKLIST_WORDS)

def get_article_content(url):
    try:
        res = http.get(url, timeout=15)
        if res.status_code != 200: return ""
        soup = BeautifulSoup(res.text, 'html.parser')
        paragraphs = [p.text.strip() for p in soup.find_all('p') if len(p.text.strip()) > 30]
        return " ".join(paragraphs)[:2500]
    except Exception:
        return ""

RSS_SOURCES = {
    "🔥 HOT TREND": ["https://vnexpress.net/rss/tin-noi-bat.rss", "https://dantri.com.vn/rss/tin-moi-nhat.rss", "https://kenh14.vn/rss/home.rss"],
    "🎬 Giải Trí - Showbiz": ["https://vnexpress.net/rss/giai-tri.rss", "https://dantri.com.vn/rss/giai-tri.rss", "https://kenh14.vn/star.rss"],
    "Tài Chính": ["https://cafef.vn/tai-chinh-ngan-hang.rss"],
    "Xã Hội": ["https://vnexpress.net/rss/thoi-su.rss", "https://kenh14.vn/xa-hoi.rss"],
    "🎓 Giới Trẻ - GenZ": ["https://kenh14.vn/hoc-duong.rss"]
}

def get_news_smart():
    init_db()
    topics = list(RSS_SOURCES.keys())
    weights = [0.35, 0.25, 0.15, 0.15, 0.10]
    for _ in range(15):
        topic = random.choices(topics, weights=weights, k=1)[0]
        try:
            res = http.get(random.choice(RSS_SOURCES[topic]), timeout=10)
            if res.status_code != 200: continue
            content = res.text.strip()
            rss_idx = content.find('<rss')
            if rss_idx != -1: content = content[rss_idx:]
            root = ET.fromstring(content)
            items = root.findall(".//item")
            if not items: continue
            item = random.choice(items[:10])
            title = item.find("title").text.strip().rsplit(" - ", 1)[0]
            link = item.find("link").text.strip()
            if not is_safe(title) or is_posted(link): continue

            # Khối tìm ảnh url (giữ lại để sau này cần mở rộng, dù post bài không cần tải)
            img_url = None
            desc = item.find("description")
            if desc is not None and desc.text:
                matches = re.findall(r'src=["\'](https?://.*?\.(?:jpg|jpeg|png|webp|jfif).*?)["\']', desc.text, re.I)
                valid_imgs = [m for m in matches if not any(x in m.lower() for x in ['icon', 'avatar', 'logo', '1x1', 'default'])]
                if valid_imgs: img_url = valid_imgs[0]
            if not img_url:
                enc = item.find("enclosure")
                if enc is not None: img_url = enc.get("url")

            content_text = get_article_content(link)
            if is_safe(content_text):
                return title, link, topic, img_url, content_text
        except Exception:
            continue
    return None, None, None, None, None

def get_global_trending_hashtags(regions=["VN", "US"], limit_per_region=2):
    """Lấy top từ khóa trending từ VN và Mỹ"""
    hashtags = set()
    for geo in regions:
        try:
            url = f"https://trends.google.com/trends/trendingsearches/daily/rss?geo={geo}"
            res = http.get(url, timeout=10)
            if res.status_code == 200:
                root = ET.fromstring(res.text)
                titles = root.findall(".//item/title")[:limit_per_region]
                for title in titles:
                    title_cased = title.text.strip().title()
                    clean_hashtag = re.sub(r'[^\w\s]', '', title_cased).replace(' ', '')
                    if clean_hashtag:
                        hashtags.add(f"#{clean_hashtag}")
        except Exception as e:
            print(f"⚠️ Lỗi lấy Google Trends ({geo}): {e}")

    hashtag_list = list(hashtags)
    random.shuffle(hashtag_list)
    return " ".join(hashtag_list)

# ================= 5. TẠO ẢNH BẢN TIN CUỐI NGÀY =================
def load_font(size):
    paths = ["arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    for p in paths:
        try: return ImageFont.truetype(p, size)
        except: pass
    return ImageFont.load_default()

# ================= 6. JOBS CHÍNH =================
def auto_seed_comment(post_id, title):
    print("💬 Đang tạo bình luận Seeding...")
    prompt = (f"Tạo bình luận mồi câu tương tác cho bài báo: '{title}'. Dưới 30 chữ. "
              "LỆNH BẮT BUỘC: KHÔNG viết câu chào hỏi. CHỈ in ra bình luận.")
    comment = call_gemini(prompt)
    if comment and post_id:
        time.sleep(10) # Delay giống người thật
        status, data = call_fb_api(f"{post_id}/comments", {'message': comment})
        if status == 200: print("✅ Đã Auto-seeding thành công!")
        else: print(f"⚠️ Lỗi bình luận mồi: {data}")

def auto_reply_job():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 💬 ĐANG ĐI TUẦN TRA VÀ TRẢ LỜI BÌNH LUẬN...")

    init_db()

    # --- BƯỚC MỚI: Tự soi gương lấy ID thật của Page ---
    me_code, me_data = get_fb_api("me")
    if me_code != 200:
        print("❌ Lỗi: Không xác định được danh tính Page.")
        return
    real_page_id = str(me_data.get('id'))

    # 1. Lấy 5 bài viết gần nhất trên Page
    code, data = get_fb_api("me/feed", {'limit': 5})

    if code != 200:
        print(f"❌ Lỗi lấy Feed từ Facebook (Code {code}): {data}")
        return

    if 'data' not in data or len(data['data']) == 0:
        print("💤 Page chưa có bài viết nào để quét bình luận.")
        return

    print(f"✅ Đã quét thấy {len(data['data'])} bài. Đang soi bằng ID thật: {real_page_id}...")

    for post in data['data']:
        post_id = post['id']

        # 2. Lấy danh sách bình luận của từng bài
        c_code, c_data = get_fb_api(f"{post_id}/comments", {'limit': 15, 'fields': 'id,message,from'})

        if c_code != 200:
            print(f"⚠️ Lỗi đọc comment bài {post_id}: {c_data}")
            continue

        if 'data' not in c_data or len(c_data['data']) == 0:
            continue

        for comment in c_data['data']:
            c_id = comment.get('id')
            msg = comment.get('message', '').strip()
            sender_id = str(comment.get('from', {}).get('id'))

            # Bỏ qua nếu tin rỗng
            if not msg:
                continue

            # Bỏ qua nếu chính Page viết (Dùng ID thật vừa soi gương được)
            if sender_id == real_page_id:
                continue

            # Bỏ qua nếu đã trả lời rồi
            if is_replied(c_id):
                continue

            print(f"🔍 Phát hiện khách thật bình luận: '{msg[:40]}...'")
            print("⏳ Đang nhờ Gemini nghĩ câu trả lời...")

            # 3. Nhờ Gemini trả lời
            prompt = (f"Bạn là admin hóm hỉnh và tận tâm của kênh TREND MỖI NGÀY.\n"
                      f"Một người dùng vừa bình luận vào bài viết của bạn: '{msg}'\n"
                      f"Nhiệm vụ: Viết một câu trả lời ngắn gọn (dưới 20 chữ), tự nhiên như người thật. "
                      f"Nếu họ khen, hãy cảm ơn hóm hỉnh. Nếu họ chê, hãy xoa dịu thông minh. Nếu họ hỏi, hãy đáp mở.\n"
                      f"LỆNH BẮT BUỘC: KHÔNG in ra dấu ngoặc kép, KHÔNG giải thích. CHỈ trả về đúng câu phản hồi.")

            reply_text = call_gemini(prompt)

            if reply_text:
                # 4. Bắn API trả lời
                r_code, r_data = call_fb_api(f"{c_id}/comments", {'message': reply_text})

                if r_code == 200:
                    print(f"✅ Đã trả lời thành công: {reply_text}")
                    # Lưu DB với cú pháp chuẩn xác đã sửa
                    execute_db("INSERT OR IGNORE INTO replied_comments (comment_id) VALUES (?)", (c_id,))
                else:
                    print(f"❌ Lỗi gửi trả lời lên FB: {r_data}")

                time.sleep(5)

def single_post_job():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🤖 BẮT ĐẦU SĂN TIN...")
    # Khai báo biến _ để bỏ qua img_url vì không cần tải ảnh nữa
    title, link, topic, _, content = get_news_smart()

    if not title:
        print("💤 Không có tin hợp lệ.")
        return

    print(f"✍️ Đang viết bài: {title}")

    # LẤY HASHTAG TRENDING
    global_hashtags = get_global_trending_hashtags()

    # CẬP NHẬT PROMPT CHO GEMINI
    prompt = (f"Bạn là admin TREND MỖI NGÀY. Viết status FB cho tin: '{title}' (Chủ đề: {topic}).\n"
              f"Nội dung: {content}\n"
              f"Tóm tắt sắc sảo, hóm hỉnh, <250 chữ. Kết bài bằng 1 câu hỏi.\n"
              f"BẮT BUỘC chèn cụm hashtag sau vào cuối bài cùng với #TRENDMOINGAY: {global_hashtags}\n"
              f"LỆNH BẮT BUỘC: KHÔNG chào hỏi, dạ vâng. CHỈ trả về status.")

    status_text = call_gemini(prompt)
    if status_text:
        msg = f"{status_text}\n"
        # Đẩy thẳng vào /feed dưới dạng Link Preview (có sẵn ảnh thumbnail siêu đẹp từ báo)
        payload = {
            'message': msg,
            'link': link
        }

        code, data = call_fb_api("me/feed", payload)
        post_id = data.get('id')

        if code == 200 and post_id:
            print(f"✅ Đã đăng Page mục Bài Viết: {title}")
            save_posted(link, title)

            real_post_id = post_id.split('_')[-1] if '_' in post_id else post_id
            send_tele(f"📣 <b>BÀI MỚI TRÊN PAGE:</b>\n{title}\n<a href='https://facebook.com/{real_post_id}'>Xem bài</a>")
            auto_seed_comment(post_id, title)
        else:
            print(f"❌ Lỗi đăng FB: {data}")

def daily_summary_job():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🌙 TẠO BẢN TIN CUỐI NGÀY...")
    today_news = get_today_news_titles()
    if len(today_news) < 3: return
    news_text = "\n".join([f"- {i[0]}" for i in today_news[:5]])
    prompt = (f"Viết ĐIỂM TIN CUỐI NGÀY:\n{news_text}\n"
              f"Giọng điệu MC thời sự dí dỏm, <300 chữ. "
              f"LỆNH BẮT BUỘC: KHÔNG chào hỏi. CHỈ in ra text.")
    summary_text = call_gemini(prompt)

    if summary_text:
        img = Image.new('RGB', (1080, 1080), color=(20, 20, 20))
        draw = ImageDraw.Draw(img)
        font = load_font(100)
        draw.text((150, 450), "📰 ĐIỂM TIN\nCUỐI NGÀY", font=font, fill=(255, 215, 0), align="center")
        img.save("summary.jpg")

        with open("summary.jpg", 'rb') as f:
            code, data = call_fb_api(f"{FB_PAGE_ID}/photos", {'message': summary_text}, files={'source': f})

        if code == 200: print("✅ Đã đăng Bản Tin Cuối Ngày!")
        if os.path.exists("summary.jpg"): os.remove("summary.jpg")
# --- MODULE TÀI CHÍNH ---
def financial_post_job():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 💰 ĐANG LÊN BẢN TIN TÀI CHÍNH...")
    try:
        url = "https://portal.vietcombank.com.vn/Usercontrols/TVPortal.TyGia/pXML.aspx"
        res = http.get(url, timeout=10)
        if res.status_code == 200:
            root = ET.fromstring(res.text)
            date_update = root.find('DateTime').text

            rates = {}
            for ex in root.findall('Exrate'):
                code = ex.get('CurrencyCode')
                if code in ['USD', 'EUR', 'GBP', 'JPY']:
                    rates[code] = {'buy': ex.get('Buy'), 'sell': ex.get('Sell')}

            # Prompt được thiết kế với góc nhìn chuyên môn nghiệp vụ
            prompt = (f"Bạn là một chuyên gia ngân hàng sắc sảo đang quản lý nội dung cho TREND MỖI NGÀY. "
                      f"Viết 1 status FB ngắn gọn (<150 chữ) cập nhật tỷ giá ngoại tệ ngày {date_update}.\n"
                      f"Dữ liệu mua/bán: USD ({rates['USD']['buy']}/{rates['USD']['sell']}), "
                      f"EUR ({rates['EUR']['buy']}/{rates['EUR']['sell']}), "
                      f"JPY ({rates['JPY']['buy']}/{rates['JPY']['sell']}).\n"
                      f"Giọng điệu chuyên nghiệp, nhận định nhanh gọn. Kết thúc bằng câu hỏi mở về diễn biến thị trường và hashtag #TRENDMOINGAY #TyGia.\n"
                      f"LỆNH BẮT BUỘC: KHÔNG chào hỏi. CHỈ in ra nội dung.")

            status_text = call_gemini(prompt)
            if status_text:
                code, data = call_fb_api("me/feed", {'message': status_text})
                if code == 200:
                    print("✅ Đã đăng bản tin tài chính!")
                    send_tele(f"📣 <b>BẢN TIN TÀI CHÍNH:</b>\n{status_text}")
                else:
                    print(f"❌ Lỗi đăng FB: {data}")
    except Exception as e:
        print(f"⚠️ Lỗi cào dữ liệu tài chính: {e}")

# --- MODULE TRIẾT LÝ MỖI NGÀY ---
def philosophy_post_job():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🧘 ĐANG LÊN BÀI TRIẾT LÝ MỖI NGÀY...")
    try:
        res = http.get("https://zenquotes.io/api/random", timeout=10)
        if res.status_code == 200:
            data = res.json()[0]
            quote = data['q']
            author = data['a']

            # Prompt được thiết kế để đúc kết sâu sắc
            prompt = (f"Bạn là người lan tỏa những giá trị triết lý sống mỗi ngày trên TREND MỖI NGÀY. Câu nói hôm nay là: '{quote}' của {author}.\n"
                      f"Nhiệm vụ:\n"
                      f"1. Dịch câu nói sang tiếng Việt với văn phong mượt mà, sâu lắng.\n"
                      f"2. Viết 1 đoạn phân tích ngắn (khoảng 3-4 câu) về ý nghĩa sâu sắc của nó và cách áp dụng vào cuộc sống hiện đại tất bật.\n"
                      f"3. Thêm hashtag #TRENDMOINGAY #TrietLyMoiNgay.\n"
                      f"LỆNH BẮT BUỘC: KHÔNG chào hỏi. chỉ in ra nội dung, trình bày đẹp mắt bằng các icon phù hợp.")

            status_text = call_gemini(prompt)
            if status_text:
                code, data = call_fb_api("me/feed", {'message': status_text})
                if code == 200:
                    print("✅ Đã đăng bài triết lý!")
                    send_tele("📣 <b>BÀI TRIẾT LÝ ĐÃ LÊN SÓNG!</b>")
    except Exception as e:
        print(f"⚠️ Lỗi lấy dữ liệu triết lý: {e}")

# --- MODULE TẠO KỊCH BẢN VIDEO VEO-3 ---
def veo_prompt_job():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🎥 ĐANG LÊN KỊCH BẢN VEO-3 CHO YOUTUBE SHORTS...")

    # Lấy 3 tin tức nóng nhất hôm nay từ Database
    today_news = get_today_news_titles()
    if len(today_news) < 1:
        print("💤 Chưa có tin nào hôm nay để làm video.")
        return

    news_text = "\n".join([f"- {i[0]}" for i in today_news[:3]])

    # Prompt chuyên sâu tối ưu cho Veo-3
    prompt = (f"Tôi cần tạo một video ngắn tổng hợp các tin tức sau:\n{news_text}\n"
              f"Nhiệm vụ: Viết một prompt tiếng Anh cực kỳ chi tiết, chuẩn điện ảnh để đưa vào mô hình AI tạo video Veo-3.\n"
              f"Yêu cầu bối cảnh: Nhân vật chính là một chú thỏ (cute rabbit character) mặc đồ vest lịch lãm đang đóng vai MC thời sự. "
              f"Chú thỏ đang ngồi tại bàn tin tức hiện đại, phía sau là màn hình hologram hiển thị bản đồ và dữ liệu. "
              f"Chỉ định rõ góc máy (ví dụ: medium shot, slow pan), ánh sáng (cinematic lighting, neon accents), và các chuyển động mượt mà.\n"
              f"LỆNH BẮT BUỘC: CHỈ in ra đoạn text tiếng Anh, không giải thích, không dùng ngoặc kép.")

    veo_prompt = call_gemini(prompt)

    if veo_prompt:
        # Sử dụng thẻ <pre> để khi hiện trên Telegram, bạn chỉ cần bấm 1 chạm là copy được toàn bộ text
        msg = f"🎬 <b>KỊCH BẢN VEO-3 (YOUTUBE SHORTS):</b>\n\n<pre>{veo_prompt}</pre>\n\n<i>Copy text trên và dán thẳng vào Veo-3 nhé!</i>"
        send_tele(msg)
        print("✅ Đã gửi kịch bản Veo-3 qua Telegram!")

# --- MODULE ẨM THỰC & AFFILIATE (Vô tận món + Tự tìm ảnh) ---
def recipe_post_job():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🍳 ĐANG LÊN BÀI: CHIỀU NAY ĂN GÌ...")

    # 1. BƯỚC ĐỘT PHÁ: Nhờ AI nghĩ ra 1 món ăn ngẫu nhiên mỗi ngày
    dish_prompt = ("Hãy gợi ý ngẫu nhiên 1 món ăn tối gia đình Việt Nam thật hấp dẫn (có thể là đồ mặn, canh, hoặc xào). "
                   "LỆNH BẮT BUỘC: CHỈ trả về đúng tên món ăn, tuyệt đối không giải thích, không dùng dấu câu.")
    dish = call_gemini(dish_prompt)

    if not dish:
        dish = "Thịt ba chỉ rang cháy cạnh" # Món backup nếu API lỗi

    print(f"💡 AI đã chọn món: {dish}")

    # 2. TỰ ĐỘNG TÌM ẢNH TRÊN BING IMAGES
    img_url = None
    try:
        print("🔍 Đang tìm ảnh minh họa cho món ăn...")
        # Mã hóa tên món ăn để đưa lên URL
        query = urllib.parse.quote(f"món ăn {dish} mâm cơm")
        bing_url = f"https://www.bing.com/images/search?q={query}"

        # Cào dữ liệu từ Bing
        res = http.get(bing_url, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')

        # Lọc lấy ảnh đầu tiên chất lượng cao
        for a in soup.find_all('a', class_='iusc'):
            m_data = a.get('m')
            if m_data:
                img_data = json.loads(m_data)
                img_url = img_data.get('murl')
                if img_url and img_url.startswith('http'):
                    break # Lấy được ảnh đầu tiên là thoát vòng lặp
    except Exception as e:
        print(f"⚠️ Lỗi tìm ảnh (Sẽ đăng bài không ảnh): {e}")

    # 3. Yêu cầu Gemini viết bài chuẩn "Food Reviewer"
    prompt = (f"Bạn là admin sành ăn của kênh TREND MỖI NGÀY. Hãy viết 1 bài đăng FB chuyên mục 'Chiều nay ăn gì?' "
              f"gợi ý thực đơn hôm nay là món: '{dish}'.\n"
              f"Văn phong rỏ dãi, gần gũi, kích thích vị giác của dân văn phòng sắp tan làm lúc 4h chiều. "
              f"Chia sẻ tóm tắt 3 bước nấu siêu nhanh gọn. Thêm hashtag #TRENDMOINGAY #ChieuNayAnGi #ThucDonGiaDinh.\n"
              f"LỆNH BẮT BUỘC: KHÔNG chào hỏi. Trình bày bằng icon bắt mắt. Câu chốt cuối cùng hãy bảo mọi người xem món đồ nghề bếp núc xịn xò ở dưới bình luận.")

    status_text = call_gemini(prompt)

    if status_text:
        # Link Affiliate lấy từ biến môi trường để không hard-code trong source.
        affiliate_link = AFFILIATE_LINK

        # 4. Đăng bài viết (Có ảnh thì dùng endpoint /photos, không có ảnh thì /feed)
        if img_url:
            code, data = call_fb_api("me/photos", {'message': status_text, 'url': img_url})
            # Khi đăng ảnh, FB sẽ trả về 'post_id' thay vì 'id'
            post_id = data.get('post_id') or data.get('id')
        else:
            code, data = call_fb_api("me/feed", {'message': status_text})
            post_id = data.get('id')

        if code == 200 and post_id:
            print(f"✅ Đã đăng gợi ý món '{dish}' thành công!")
            send_tele(f"📣 <b>CHUYÊN MỤC ẨM THỰC LÊN SÓNG:</b>\n{dish}")

            # 5. Tự động Seeding bình luận (Nhờ AI biến hóa mỗi ngày)
            time.sleep(10) # Chờ FB xử lý ảnh xong

            # Nhờ Gemini viết bình luận mồi riêng cho món ăn hôm nay
            seed_prompt = (f"Viết 1 câu bình luận mồi (dưới 20 chữ) thật tự nhiên, dân dã để thả vào dưới bài viết về món '{dish}'. "
                           f"Mục đích: Khen độ ngon, khơi gợi sự thèm ăn hoặc đặt câu hỏi nhẹ nhàng để người xem vào tương tác. "
                           f"LỆNH BẮT BUỘC: KHÔNG dùng ngoặc kép, KHÔNG giải thích, thêm 1 icon cho sinh động.")
            seed_msg = call_gemini(seed_prompt)

            # Nếu AI bị lỗi/quá tải thì dùng câu dự phòng ngẫu nhiên
            if not seed_msg:
                backup_seeds = [
                    f"Trời mưa lất phất mà có đĩa {dish} này thì tốn cơm phải biết mấy bà nhỉ! 🤤",
                    f"Món {dish} này nhà mình con nít người lớn đều mê tít, dễ làm cực kỳ! 🍳",
                    f"Team đạo {dish} điểm danh nhẹ ở đây cái nàooo! 🙋‍♀️"
                ]
                seed_msg = random.choice(backup_seeds)

            # Chỉ gửi 1 bình luận. Nếu có AFFILIATE_LINK thì chèn link vào bình luận.
            comment_message = seed_msg
            if affiliate_link:
                comment_message = f"{seed_msg}\n\n🔗 {affiliate_link}"

            c_code, c_data = call_fb_api(
                f"{post_id}/comments",
                {'message': comment_message}
            )

            if c_code == 200:
                if affiliate_link:
                    print("✅ Đã đăng bình luận mồi kèm link Affiliate thành công!")
                else:
                    print("✅ Đã đăng bình luận mồi thành công (chưa cấu hình AFFILIATE_LINK).")
            else:
                print(f"❌ Lỗi thả link Affiliate: {c_data}")
        else:
            print(f"❌ Lỗi đăng bài ẩm thực: {data}")

# --- MODULE GIẢI TRÍ & TẤU HÀI ---
def entertainment_post_job():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🤡 ĐANG LÊN BÀI GIẢI TRÍ / TẤU HÀI...")

    # 1. Bốc thăm ngẫu nhiên chủ đề và thái độ mỗi ngày
    topics = [
        "nỗi khổ đi làm, deadline dí ngập đầu, sếp hối",
        "tình trạng rỗng ví, chưa hết tháng đã hết tiền",
        "tình yêu mập mờ, ế bằng thực lực, bị fomo tình cảm",
        "nỗi ám ảnh cân nặng, miệng nói giảm cân nhưng tay cầm ly trà sữa",
        "thức khuya lướt điện thoại, sáng dậy không nổi",
        "những triết lý vô tri, ngớ ngẩn nhưng nghe cực kỳ hợp lý"
    ]

    tones = [
        "xéo xắt, châm biếm sâu cay",
        "than vãn một cách đáng yêu, tự dìm hàng bản thân",
        "thả thính dạo nhưng cực kỳ củ chuối",
        "vô tri, lầy lội, đọc xong thấy tốn thời gian nhưng vẫn buồn cười",
        "tỏ ra trưởng thành nhưng chốt câu lại cực kỳ trẻ trâu"
    ]

    topic = random.choice(topics)
    tone = random.choice(tones)

    print(f"🎯 Đề bài hôm nay: Viết về '{topic}' với thái độ '{tone}'")

    # Nhờ Gemini viết dựa trên đề bài đã bốc thăm
    prompt = (f"Bạn là admin Gen Z của kênh TREND MỖI NGÀY. "
              f"Hãy viết 1 status tấu hài (dưới 40 chữ) về chủ đề: '{topic}'.\n"
              f"Văn phong BẮT BUỘC: {tone}.\n"
              f"LỆNH BẮT BUỘC: KHÔNG dùng ngoặc kép, KHÔNG giải thích. Kèm theo hashtag #TRENDMOINGAY #GiaiTri và vài icon phù hợp.")

    status_text = call_gemini(prompt)

    # 2. Tìm một chiếc Meme ngẫu nhiên để tăng độ mặn (Ưu tiên meme mèo vì dễ viral và an toàn)
    img_url = None
    try:
        print("🔍 Đang đi lùng ảnh Meme hài hước...")
        # Tìm ngẫu nhiên các loại meme khác nhau mỗi ngày
        meme_keywords = ["meme mèo hài hước", "ảnh chế đi làm hài hước", "meme khóc thét", "meme vô tri"]
        keyword = random.choice(meme_keywords)

        query = urllib.parse.quote(keyword)
        bing_url = f"https://www.bing.com/images/search?q={query}"

        res = http.get(bing_url, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')

        # Trộn ngẫu nhiên danh sách ảnh tìm được để không bị trùng bài cũ
        images = soup.find_all('a', class_='iusc')
        random.shuffle(images)

        for a in images[:10]: # Thử tối đa 10 ảnh
            m_data = a.get('m')
            if m_data:
                img_data = json.loads(m_data)
                img_url = img_data.get('murl')
                if img_url and img_url.startswith('http'):
                    break
    except Exception as e:
        print(f"⚠️ Lỗi tìm Meme: {e}")

    # 3. Đăng bài lên Facebook
    if img_url:
        code, data = call_fb_api("me/photos", {'message': status_text, 'url': img_url})
    else:
        code, data = call_fb_api("me/feed", {'message': status_text})

    if code == 200:
        print("✅ Đã đăng bài giải trí thành công!")
        send_tele("📣 <b>CHUYÊN MỤC TẤU HÀI ĐÃ LÊN SÓNG!</b>")
    else:
        print(f"❌ Lỗi đăng bài giải trí: {data}")

# ================= 7. KHỞI CHẠY =================
if __name__ == "__main__":
    raise SystemExit(
        "autobot.py là module legacy. Hãy chạy: python hardening_runner.py <action>"
    )