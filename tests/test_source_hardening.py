from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_FILES = ["autobot.py", "runner.py", "autobotvideo.py", "hardening_runner.py"]


class SourceHardeningTests(unittest.TestCase):
    def test_production_sources_do_not_disable_tls_verification(self):
        offenders = []
        for relative in PRODUCTION_FILES:
            text = (ROOT / relative).read_text(encoding="utf-8")
            if "verify=False" in text or "disable_warnings" in text:
                offenders.append(relative)
        self.assertEqual(offenders, [], f"TLS verification bypass remains in: {offenders}")


if __name__ == "__main__":
    unittest.main()
