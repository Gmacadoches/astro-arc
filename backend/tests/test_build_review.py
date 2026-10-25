"""build_review.py files every generation in the archive, and
astro-arc-archive-run makes sure a run it fails on is filed later instead of
lost. astro-arc-generate only warns when filing fails, so a crash here drops
runs from the panel's history without failing them: these run both end to end
in a throwaway home.

    python3 -m unittest discover backend/tests
"""

import base64
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from throwaway import throwaway_home

BACKEND = Path(__file__).resolve().parents[2] / "backend"
BIN = BACKEND / "bin"

# A 1x1 PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def write_run(work, period_key):
    """A run's inputs, as astro-arc-generate hands them over: {flag: path}."""
    work.mkdir(parents=True)
    (work / "reading.json").write_text(json.dumps({
        "natal": {"planets": {"sun": {"sign": "Leo"}, "moon": {"sign": "Pisces"}}},
        "arc": {"frequency": "daily", "moonSign": "Aries", "moonPhase": "Waxing Gibbous"}}))
    (work / "meta.json").write_text(json.dumps({
        "artStyle": "engraving", "distillation": "d", "narrativePosition": "n",
        "conceptTags": ["geological"], "prompt": "p"}))
    (work / "image.png").write_bytes(PNG)
    return ["--reading", work / "reading.json", "--meta", work / "meta.json",
            "--image", work / "image.png", "--label", period_key, "--period-key", period_key]


class BuildReviewTest(unittest.TestCase):
    def setUp(self):
        self.home, self.env = throwaway_home(self)
        self.index_file = self.home / ".local/share/astro-arc/generations.json"

    def index(self):
        return json.loads(self.index_file.read_text())

    def build_review(self, *args):
        return subprocess.run([sys.executable, BIN / "build_review.py", *args],
                              env=self.env, capture_output=True, text=True)

    def test_a_generation_with_a_style_is_filed_in_the_archive(self):
        run = self.build_review(*write_run(self.home / "run", "2026-10-24T20"))

        self.assertEqual(run.returncode, 0, run.stderr)
        index = self.index()
        self.assertEqual([e["periodKey"] for e in index], ["2026-10-24T20"])
        self.assertEqual(index[0]["artStyle"], "engraving")
        self.assertTrue(index[0]["artStyleLabel"])

    def test_a_run_filed_late_keeps_its_time_and_its_place(self):
        # createdAt is written in local time, so the zone is pinned to the
        # offsets below (POSIX signs run backwards: Etc/GMT+6 is UTC-6).
        self.env["TZ"] = "Etc/GMT+6"
        for key, at in [("2026-10-24T18", "2026-10-24T18:04:10-06:00"),
                        ("2026-10-24T20", "2026-10-24T20:01:06-06:00"),
                        ("2026-10-24T19", "2026-10-24T19:07:53-06:00")]:
            run = self.build_review(*write_run(self.home / key, key), "--created-at", at)
            self.assertEqual(run.returncode, 0, run.stderr)

        index = self.index()
        self.assertEqual([e["periodKey"] for e in index],
                         ["2026-10-24T20", "2026-10-24T19", "2026-10-24T18"])
        late = index[1]
        self.assertEqual(late["createdAt"], "2026-10-24T19:07:53")
        self.assertTrue(late["id"].startswith("20261024-190753-"))
        self.assertEqual(Path(late["path"]).parent.name, late["id"])


class ArchiveRunTest(unittest.TestCase):
    """astro-arc-archive-run against a copy of the backend whose build_review.py
    can be broken and mended."""

    def setUp(self):
        self.home, self.env = throwaway_home(self)
        backend = self.home / "backend"
        shutil.copytree(BIN, backend / "bin", ignore=shutil.ignore_patterns("__pycache__"))
        (backend / "pipeline").symlink_to(BACKEND / "pipeline")
        self.bin = backend / "bin"
        self.pending = self.home / ".local/state/omarchy/astro-arc/pending-archive"
        self.index_file = self.home / ".local/share/astro-arc/generations.json"

    def archive_run(self, *args):
        return subprocess.run([self.bin / "astro-arc-archive-run", *args],
                              env=self.env, capture_output=True, text=True)

    def test_a_run_that_cannot_be_filed_waits_and_is_filed_by_the_next(self):
        good = (self.bin / "build_review.py").read_text()
        (self.bin / "build_review.py").write_text("raise ImportError('broken')\n")
        first = write_run(self.home / "first", "2026-10-24T19")

        failed = self.archive_run(*first)

        self.assertEqual(failed.returncode, 1)
        self.assertIn("kept in", failed.stderr)
        self.assertFalse(self.index_file.exists())
        # The state files are overwritten by the next run in the period.
        for name in ("reading.json", "meta.json", "image.png"):
            (self.home / "first" / name).unlink()
        [kept] = list(self.pending.iterdir())

        (self.bin / "build_review.py").write_text(good)
        second = self.archive_run(*write_run(self.home / "second", "2026-10-24T20"))

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertTrue(second.stdout.strip().endswith("index.html"))
        self.assertEqual(list(self.pending.iterdir()), [])
        index = json.loads(self.index_file.read_text())
        self.assertEqual([e["periodKey"] for e in index], ["2026-10-24T20", "2026-10-24T19"])
        self.assertEqual(index[1]["createdAt"].replace("-", "").replace("T", "-").replace(":", ""),
                         kept.name.removesuffix("-2026-10-24T19"))

    def test_a_kept_run_already_in_the_index_is_not_filed_twice(self):
        first = write_run(self.home / "first", "2026-10-24T19")
        self.assertEqual(self.archive_run(*first).returncode, 0)
        [entry] = json.loads(self.index_file.read_text())
        # As if build_review.py had failed after writing the index.
        kept = self.pending / "20261024-190753-2026-10-24T19"
        kept.mkdir(parents=True)
        for name in ("reading.json", "meta.json", "image.png"):
            shutil.copy(self.home / "first" / name, kept / name)
        (kept / "run.json").write_text(json.dumps({
            "label": "x", "createdAt": entry["createdAt"], "periodKey": "2026-10-24T19", "cost": None}))

        again = self.archive_run(*write_run(self.home / "second", "2026-10-24T20"))

        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertFalse(kept.exists())
        self.assertEqual(len(json.loads(self.index_file.read_text())), 2)


if __name__ == "__main__":
    unittest.main()
