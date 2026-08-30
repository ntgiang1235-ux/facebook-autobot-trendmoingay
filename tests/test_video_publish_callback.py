import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autobotvideo


class VideoPublishCallbackTests(unittest.TestCase):
    def test_successful_video_publish_emits_ledger_metadata_once(self):
        video = {
            "id": 789,
            "url": "https://www.pexels.com/video/789/",
            "user": {"name": "Creator"},
            "duration": 12,
        }
        file_info = {"link": "https://cdn.example.com/video.mp4"}
        callback = Mock()

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "video.mp4"

            def fake_download(_url, path):
                path.write_bytes(b"video")

            with patch.object(autobotvideo, "TEMP_VIDEO", target), patch.object(
                autobotvideo, "validate_config"
            ), patch.object(autobotvideo, "init_db"), patch.object(
                autobotvideo,
                "pick_unposted_video",
                return_value=(video, file_info, "Vietnamese food"),
            ), patch.object(
                autobotvideo, "download_video", side_effect=fake_download
            ), patch.object(
                autobotvideo, "generate_caption", return_value="Caption reel"
            ), patch.object(
                autobotvideo, "upload_facebook", return_value={"id": "video-789"}
            ), patch.object(autobotvideo, "save_posted"), patch.object(
                autobotvideo, "send_telegram"
            ):
                autobotvideo.video_post_job(dry_run=False, on_published=callback)

        callback.assert_called_once()
        kwargs = callback.call_args.kwargs
        self.assertEqual(kwargs["endpoint"], "me/videos")
        self.assertEqual(kwargs["request_data"], {"message": "Caption reel"})
        self.assertEqual(kwargs["response"], {"id": "video-789"})
        self.assertEqual(kwargs["topic_text"], "Vietnamese food")
        self.assertEqual(kwargs["source_url"], "https://www.pexels.com/video/789/")
        self.assertEqual(kwargs["format_type"], "video")


if __name__ == "__main__":
    unittest.main()
