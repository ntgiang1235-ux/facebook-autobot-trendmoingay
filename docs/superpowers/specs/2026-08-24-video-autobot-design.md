# Video AutoBot Design

## Goal
Integrate a copyright-safer Facebook video posting job into the existing `facebook-autobot-trendmoingay` repository and run it four times per day with GitHub Actions.

## Architecture
- Keep `autobot.py` and `runner.py` for existing text/image jobs.
- Add a focused `autobotvideo.py` for video discovery, persistence, caption generation, download, upload, cleanup, and Telegram notification.
- Use Pexels Video API as the primary video source. Do not download/repost YouTube videos or use transformations intended to evade platform detection.
- Use the existing Turso database with a new `posted_videos` table so GitHub Actions runners remember previously posted source videos.
- Reuse existing GitHub Secrets: `GEMINI_API_KEYS`, `FB_ACCESS_TOKEN`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`, plus `PEXELS_API_KEY`.

## Video selection
- Search Pexels through `GET https://api.pexels.com/v1/videos/search`.
- Prefer portrait videos, `size=small`, locale `vi-VN`.
- Rotate across safe broad topics such as funny animals, office life, food, travel, nature, technology, fitness, and lifestyle.
- Only accept videos between 5 and 90 seconds.
- Prefer MP4 renditions at 720p/1080p and avoid downloading unnecessarily large files.
- Skip source video IDs already stored in Turso.

## Posting flow
1. Validate required environment variables.
2. Initialize the `posted_videos` table.
3. Search multiple randomized topics until an unseen video is found.
4. Download the selected Pexels MP4 to a temporary local file.
5. Generate one concise Vietnamese Facebook caption with Gemini.
6. Append Pexels creator/source attribution to the caption.
7. Upload to Facebook using Graph API endpoint `me/videos` with binary file upload.
8. On successful Facebook upload, record the Pexels video ID in Turso and notify Telegram.
9. Remove temporary files in all success/failure paths.
10. Raise an exception on operational failure so GitHub Actions reports a failed run instead of a false green result.

## Schedule
Run the video job four times every day in Vietnam time:
- 09:30
- 12:45
- 17:30
- 21:00

The workflow keeps manual `video` dispatch available for testing.

## Safety / content constraints
- No YouTube download, cookies, proxy, `yt_dlp`, or source-bypass behavior.
- No mirroring/speed/crop/color modifications intended to defeat copyright or content-matching systems.
- Pexels source/creator attribution is appended when metadata is available.

## Verification
- Python syntax compile for `autobotvideo.py`.
- Workflow contains `video` manual option, the four UTC cron equivalents, `PEXELS_API_KEY`, and video dispatch.
- Existing text/image actions remain routed through `runner.py` unchanged.
