import unittest
from unittest.mock import patch

import requests

from app.http import VerifiedSession, secure_session_from


class SecureHttpTests(unittest.TestCase):
    def test_verified_session_overrides_verify_false(self):
        session = VerifiedSession()
        with patch.object(requests.Session, "request", return_value="ok") as request:
            result = session.request("GET", "https://example.com", verify=False)

        self.assertEqual(result, "ok")
        self.assertTrue(request.call_args.kwargs["verify"])

    def test_secure_session_copies_existing_headers(self):
        existing = requests.Session()
        existing.headers.update({"User-Agent": "TRENDMOINGAY-Test", "X-Test": "yes"})

        secured = secure_session_from(existing)

        self.assertIsInstance(secured, VerifiedSession)
        self.assertEqual(secured.headers["User-Agent"], "TRENDMOINGAY-Test")
        self.assertEqual(secured.headers["X-Test"], "yes")


if __name__ == "__main__":
    unittest.main()
