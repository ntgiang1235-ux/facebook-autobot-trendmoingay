import unittest
from unittest.mock import patch

import app.notifications as notifications


class FakeResponse:
    def raise_for_status(self):
        return None


class NotificationsTests(unittest.TestCase):
    def test_send_message_is_noop_without_config(self):
        with patch.object(notifications, "TELEGRAM_TOKEN", ""), patch.object(
            notifications, "TELEGRAM_CHAT_ID", ""
        ), patch.object(notifications.http, "post") as post:
            result = notifications.send_message("hello")

        self.assertFalse(result)
        post.assert_not_called()

    def test_send_failure_contains_action_error_and_run_url(self):
        with patch.object(notifications, "TELEGRAM_TOKEN", "bot-token"), patch.object(
            notifications, "TELEGRAM_CHAT_ID", "123"
        ), patch.object(notifications.http, "post", return_value=FakeResponse()) as post:
            result = notifications.send_failure(
                "video", RuntimeError("Facebook HTTP 500"), "https://github.com/example/run/1"
            )

        self.assertTrue(result)
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.telegram.org/botbot-token/sendMessage")
        self.assertEqual(kwargs["timeout"], 15)
        text = kwargs["json"]["text"]
        self.assertIn("video", text)
        self.assertIn("Facebook HTTP 500", text)
        self.assertIn("https://github.com/example/run/1", text)

    def test_notification_network_failure_is_swallowed(self):
        with patch.object(notifications, "TELEGRAM_TOKEN", "bot-token"), patch.object(
            notifications, "TELEGRAM_CHAT_ID", "123"
        ), patch.object(notifications.http, "post", side_effect=OSError("network down")):
            result = notifications.send_message("hello")

        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
