from pathlib import Path
import unittest


class DispatchWatchdogWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.path = Path('.github/workflows/dispatch-watchdog.yml')

    def test_watchdog_is_an_independent_dispatch_only_wake(self):
        self.assertTrue(self.path.exists(), 'dispatch watchdog workflow must exist')
        text = self.path.read_text(encoding='utf-8')
        self.assertIn('cron: "22,52 * * * *"', text)
        self.assertIn('group: facebook-autobot-dispatch-watchdog', text)
        self.assertIn('cancel-in-progress: false', text)
        self.assertIn('python hardening_runner.py "dispatch"', text)
        self.assertNotIn('python hardening_runner.py "planner"', text)
        self.assertNotIn('ACTION=', text)

    def test_watchdog_has_required_runtime_secrets(self):
        text = self.path.read_text(encoding='utf-8')
        for secret in (
            'GEMINI_API_KEYS',
            'FB_ACCESS_TOKEN',
            'FB_PAGE_ID',
            'TELEGRAM_TOKEN',
            'TELEGRAM_CHAT_ID',
            'BLACKLIST_WORDS',
            'AFFILIATE_LINK',
            'PEXELS_API_KEY',
            'TURSO_DATABASE_URL',
            'TURSO_AUTH_TOKEN',
        ):
            self.assertIn(f'{secret}: ${{{{ secrets.{secret} }}}}', text)


if __name__ == '__main__':
    unittest.main()
