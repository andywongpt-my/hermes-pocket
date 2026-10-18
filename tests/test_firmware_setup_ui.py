"""Offline phone setup UI behavior and generated embedded-page parity."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FirmwareSetupUITests(unittest.TestCase):
    def test_phone_setup_ui_offline(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("owner-installed Node.js unavailable")
        result = subprocess.run(
            [node, str(ROOT / "firmware/tests/test_setup_ui.js"),
             str(ROOT / "firmware/main/pocket_setup.html")],
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("offline setup UI tests", result.stdout)

    def test_embedded_page_matches_html_with_nonce_injection(self):
        page = (ROOT / "firmware/main/pocket_setup.html").read_text(encoding="utf-8")
        header = (ROOT / "firmware/main/pocket_setup_page.h").read_text(encoding="utf-8")
        parts = []
        for name in ("setup_html_before", "setup_html_after"):
            match = re.search(r"static const char " + name + r"\[\] = (\"(?:[^\"\\]|\\.)*\");", header)
            self.assertIsNotNone(match, name + " must be an escaped C string")
            parts.append(json.loads(match.group(1)))
        self.assertEqual(page.count("__POCKET_NONCE__"), 1)
        self.assertEqual(parts[0] + "__POCKET_NONCE__" + parts[1], page)
        self.assertTrue(parts[0].endswith('name="pocket-nonce" content="'))
        self.assertTrue(parts[1].startswith('\">'))


if __name__ == "__main__":
    unittest.main()
