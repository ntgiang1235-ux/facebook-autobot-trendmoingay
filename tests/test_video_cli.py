import sys
import unittest
from unittest.mock import patch

import autobotvideo


class VideoCliTests(unittest.TestCase):
    def test_direct_run_is_blocked_in_favor_of_hardening_runner(self):
        with patch.object(sys, "argv", ["autobotvideo.py", "run"]), patch.object(
            autobotvideo, "video_post_job"
        ) as video_job:
            with self.assertRaises(SystemExit) as raised:
                autobotvideo.main()
        video_job.assert_not_called()
        self.assertIn("hardening_runner.py video", str(raised.exception))

    def test_dry_run_remains_available(self):
        with patch.object(sys, "argv", ["autobotvideo.py", "dry-run"]), patch.object(
            autobotvideo, "video_post_job"
        ) as video_job:
            autobotvideo.main()
        video_job.assert_called_once_with(dry_run=True)


if __name__ == "__main__":
    unittest.main()
