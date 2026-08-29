# Hardening & Reliability Design

## Goal

Nâng bot từ mức script automation cá nhân lên mức vận hành ổn định, có thể quan sát và bảo trì lâu dài mà không thay đổi kiến trúc hạ tầng GitHub Actions + Turso + Facebook Graph API + Gemini + Pexels.

## Scope

1. Chuẩn hóa kết quả job để lỗi nghiệp vụ thật làm GitHub Actions fail thay vì false-green.
2. Chạy toàn bộ test trong CI và bổ sung test cho error contract, retry và các helper quan trọng.
3. Loại bỏ việc vô hiệu hóa TLS verification.
4. Pin dependency và thêm Dependabot cho Python/GitHub Actions.
5. Tách các phần hạ tầng dùng chung ra module nhỏ, trước mắt là result contract, database wrapper và notification; không viết lại toàn bộ job.
6. Thêm bảng `job_runs` trong Turso để theo dõi started/success/skipped/failed, cùng Telegram failure alert.
7. Giữ nguyên lịch cron, số lần chạy, nội dung các job và nguồn dữ liệu hiện có trừ khi cần sửa để đảm bảo error contract.

## Architecture

### Job contract

Mọi action được chạy qua một orchestration layer duy nhất. Một job phải kết thúc ở một trong ba trạng thái:

- `success`: công việc đã hoàn thành như dự kiến.
- `skipped`: không có nội dung hợp lệ để đăng; đây không phải lỗi.
- `failed`: dependency/API/configuration hoặc nghiệp vụ không hoàn thành; process phải exit non-zero.

Runner chịu trách nhiệm ghi `job_runs`, gửi Telegram failure alert và re-raise lỗi để GitHub Actions đỏ.

### Database

Tạo `app/db.py` làm entry point duy nhất cho Turso. `db.execute()` dùng retry hiện có cho lỗi 502/503/504/timeout. Schema initialization bổ sung bảng:

```sql
CREATE TABLE IF NOT EXISTS job_runs (
  run_key TEXT PRIMARY KEY,
  action TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  detail TEXT
)
```

Không thay đổi bảng `posted_news`, `replied_comments`, `posted_videos`.

### Observability

Runner tạo `run_key` từ action + timestamp. Khi bắt đầu ghi `started`; khi kết thúc ghi `success` hoặc `skipped`; exception ghi `failed`, gửi Telegram alert có action, thời gian và lỗi ngắn, rồi re-raise.

Nếu Turso đang lỗi đến mức không ghi được trạng thái failed, runner vẫn phải re-raise lỗi gốc; notification không được che lỗi chính.

### Security

Bỏ `urllib3.disable_warnings()` và mọi `verify=False`. Requests dùng certificate verification mặc định.

### CI and dependencies

CI riêng chạy trên `push` và `pull_request`:

- Python 3.11
- install `requirements.txt`
- `python -m unittest discover -s tests -v`
- `python -m compileall -q .`

Pin version trực tiếp trong `requirements.txt`. Dependabot kiểm tra `pip` và `github-actions` hàng tuần.

## Compatibility constraints

- Không đổi tên GitHub Secrets hiện tại.
- Không đổi cron hiện tại.
- Không đổi Facebook Graph API version trong phase này.
- Không thêm server/VPS/Redis/Celery/Docker.
- Không làm thay đổi nội dung public của các job nếu không cần thiết cho reliability.
- Không xóa các wrapper cũ cho đến khi orchestration mới đã được CI kiểm chứng.

## Testing strategy

Test không gọi Facebook/Gemini/Pexels/Turso thật. Test orchestration bằng fake job functions và fake recorder/notifier. Test DB retry giữ test 502 hiện có. Test security scan bảo đảm source không còn `verify=False` hoặc `disable_warnings`. Test workflow bằng compile/static tests trong GitHub Actions.

## Rollout

Thực hiện trên `codex/hardening-reliability`. CI phải xanh trên branch. Sau đó mới fast-forward `main` tới commit đã kiểm chứng. Scheduled workflow hiện tại tiếp tục là kiểm tra production sau merge; không thay đổi secrets hay database thủ công.
