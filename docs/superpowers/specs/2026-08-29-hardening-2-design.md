# Hardening Phase 2 Design

## Goal

Hoàn thiện lớp hardening còn dang dở để bot có một đường chạy production duy nhất, không còn fallback ảnh không kiểm soát, mọi publish job có outcome rõ ràng, và CI trở thành gate trước khi code vào `main`.

## Scope

1. Xóa `urllib3.disable_warnings()` và mọi `verify=False` khỏi source legacy; CLI legacy không được tạo đường chạy production riêng mà phải hướng người dùng tới `hardening_runner.py`.
2. Loại Bing Images fallback khỏi `runner.py`; fallback ảnh chỉ dùng nguồn được cấu hình rõ ràng, nếu không có ảnh thì đăng text-only.
3. Chuẩn hóa `fun` và `recipe` để publish thành công chỉ khi Facebook trả HTTP 200 và có `id`/`post_id`; bình luận mồi sau bài recipe vẫn best-effort.
4. Production workflow không chạy unit tests mỗi cron; CI riêng vẫn chạy full tests + compileall trên push/PR.
5. Bật bảo vệ `main` yêu cầu CI `test` thành công trước khi merge/push theo khả năng GitHub repository hiện có.

## Constraints

- Không đổi cron, số job/ngày, secret names, Facebook Graph API version hoặc hạ tầng GitHub Actions + Turso.
- Không thêm VPS/Docker/Redis/Celery.
- Không thay đổi nội dung public của các job ngoài việc bỏ Bing fallback.
- Pexels vẫn là nguồn ảnh chính; khi không có ảnh phù hợp thì text-only thay vì lấy ảnh web không xác định bản quyền.

## Testing

- Thêm regression test quét source để không còn `verify=False`/`disable_warnings` trong production files.
- Thêm test cho `fun`/`recipe` success/failure contract.
- Thêm test xác nhận workflow production không chạy unit tests nhưng CI workflow vẫn chạy full suite.
- Chạy `python -m unittest discover -s tests -v` và `python -m compileall -q .` trên branch trước khi đưa vào `main`.
