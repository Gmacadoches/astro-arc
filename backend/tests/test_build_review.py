"""build_review.py files every generation in the archive. astro-arc-generate
only warns when it fails, so a crash here loses each run from the panel's
history without failing the run: this runs it end to end in a throwaway home.

    python3 -m unittest discover backend/tests
"""

import base64
import json
import subprocess
import sys
import unittest
from pathlib import Path

from throwaway import throwaway_home

BIN = Path(__file__).resolve().parents[2] / "backend" / "bin"

# A 1x1 PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


class BuildReviewTest(unittest.TestCase):
    def test_a_generation_with_a_style_is_filed_in_the_archive(self):
        home, env = throwaway_home(self)
        work = home / "run"
        work.mkdir()
        (work / "reading.json").write_text(json.dumps({
            "natal": {"planets": {"sun": {"sign": "Leo"}, "moon": {"sign": "Pisces"}}},
            "arc": {"frequency": "daily", "moonSign": "Aries", "moonPhase": "Waxing Gibbous"}}))
        (work / "meta.json").write_text(json.dumps({
            "artStyle": "engraving", "distillation": "d", "narrativePosition": "n",
            "conceptTags": ["geological"], "prompt": "p"}))
        (work / "image.png").write_bytes(PNG)

        run = subprocess.run(
            [sys.executable, BIN / "build_review.py",
             "--reading", work / "reading.json", "--meta", work / "meta.json",
             "--image", work / "image.png", "--label", "test", "--period-key", "2026-10-24T20"],
            env=env, capture_output=True, text=True)

        self.assertEqual(run.returncode, 0, run.stderr)
        index = json.loads((home / ".local/share/astro-arc/generations.json").read_text())
        self.assertEqual([e["periodKey"] for e in index], ["2026-10-24T20"])
        self.assertEqual(index[0]["artStyle"], "engraving")
        self.assertTrue(index[0]["artStyleLabel"])


if __name__ == "__main__":
    unittest.main()
