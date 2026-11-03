"""Seeding: every style is the user's file, and the shipped ones are seeds
(decision record 2026-11-styles-owned). Each test runs the real commands in a
throwaway home, since seeding writes the styles folder and the settings.

    python3 -m unittest discover backend/tests
"""

import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from throwaway import throwaway_home

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "backend" / "bin"
SEED_DIR = ROOT / "backend" / "pipeline" / "styles"
with open(ROOT / "backend" / "pipeline" / "starter-styles.toml", "rb") as f:
    STARTER = tomllib.load(f)
VISIBLE = set(STARTER["visible"])


class SeedingTest(unittest.TestCase):
    def setUp(self):
        self.home, self.env = throwaway_home(self)
        self.styles = self.home / ".config" / "astro-arc" / "styles"
        self.env["XDG_CONFIG_HOME"] = str(self.home / ".config")

    def run_bin(self, *args, ok=True):
        result = subprocess.run([sys.executable, *map(str, args)], env=self.env,
                                capture_output=True, text=True, timeout=60)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def seed(self, seed_dir=SEED_DIR):
        """seed() against `seed_dir`, standing in for another release."""
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import style_seeds, json; "
                "print(json.dumps(style_seeds.seed(seed_dir=sys.argv[2])))")
        return json.loads(self.run_bin("-c", code, BIN, seed_dir).stdout)

    def config(self):
        return json.loads(self.run_bin(BIN / "astro-arc-config").stdout)

    def style(self, *args, ok=True):
        return self.run_bin(BIN / "astro-arc-style", *args, ok=ok)

    def release(self):
        """A copy of the seeds to change, as a later release would."""
        folder = Path(tempfile.mkdtemp(prefix="astro-arc-seeds-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        for path in SEED_DIR.iterdir():
            shutil.copy(path, folder / path.name)
        return folder

    def test_fresh_install_shows_the_starter_set(self):
        done = self.seed()
        seeds = sorted(p.stem for p in SEED_DIR.glob("*.toml"))
        self.assertEqual(sorted(done["copied"]), seeds)
        self.assertEqual(sorted(p.stem for p in self.styles.glob("*.toml")), seeds)
        config = self.config()
        self.assertEqual(config["artStyle"], "marbled-paper")
        self.assertEqual(set(config["hiddenStyles"]), set(seeds) - VISIBLE)
        # Nothing is "new" on the first seeding: it is all new.
        self.assertEqual(json.loads((self.styles / ".seeds.json").read_text())["new"], [])

    def test_seeding_twice_changes_nothing(self):
        self.seed()
        before = (self.styles / ".seeds.json").read_text()
        done = self.seed()
        self.assertFalse(any(done.values()), done)
        self.assertEqual((self.styles / ".seeds.json").read_text(), before)

    def test_the_selected_style_is_never_hidden(self):
        self.run_bin(BIN / "astro-arc-config", "--set-art-style", "cyberpunk")
        self.seed()
        self.assertNotIn("cyberpunk", self.config()["hiddenStyles"])

    def test_a_users_own_file_is_adopted_not_overwritten(self):
        self.styles.mkdir(parents=True)
        shutil.copy(SEED_DIR / "cosmic.toml", self.styles / "cosmic.toml")
        (self.styles / "engraving.toml").write_text('label = "Mine"\n')
        done = self.seed()
        self.assertIn("engraving", done["adopted"])
        self.assertEqual((self.styles / "engraving.toml").read_text(), 'label = "Mine"\n')
        # Already there, so not hidden, though it is not a starter key.
        self.assertNotIn("cosmic", self.config()["hiddenStyles"])
        rows = {r["key"]: r for r in json.loads(self.run_bin(BIN / "style_catalog.py").stdout)}
        self.assertTrue(rows["engraving"]["changed"])
        self.assertFalse(rows["cosmic"]["changed"])

    def test_a_release_updates_untouched_files_only(self):
        self.seed()
        (self.styles / "ukiyoe.toml").write_text((self.styles / "ukiyoe.toml").read_text() + "# mine\n")
        release = self.release()
        for key in ("ukiyoe", "cosmic"):
            path = release / f"{key}.toml"
            path.write_text(path.read_text() + "# fixed in a release\n")
        done = self.seed(release)
        self.assertEqual(done["updated"], ["cosmic.toml"])
        self.assertTrue((self.styles / "cosmic.toml").read_text().endswith("# fixed in a release\n"))
        self.assertTrue((self.styles / "ukiyoe.toml").read_text().endswith("# mine\n"))

    def test_a_new_preview_does_not_stop_charter_fixes(self):
        self.seed()
        (self.styles / "cosmic.jpg").write_bytes(b"my own preview")
        release = self.release()
        (release / "cosmic.toml").write_text((release / "cosmic.toml").read_text() + "# fixed\n")
        self.assertEqual(self.seed(release)["updated"], ["cosmic.toml"])
        self.assertEqual((self.styles / "cosmic.jpg").read_bytes(), b"my own preview")

    def test_a_style_a_release_adds_arrives_hidden_and_new(self):
        self.seed()
        release = self.release()
        shutil.copy(release / "cosmic.toml", release / "brand-new.toml")
        shutil.copy(release / "cosmic.jpg", release / "brand-new.jpg")
        self.seed(release)
        self.assertIn("brand-new", self.config()["hiddenStyles"])
        self.assertEqual(json.loads((self.styles / ".seeds.json").read_text())["new"], ["brand-new"])

    def test_deleted_stays_deleted_until_restored(self):
        self.seed()
        self.run_bin(BIN / "astro-arc-config", "--favorite-style", "cosmic", "true")
        self.assertTrue(json.loads(self.style("delete", "cosmic").stdout)["ok"])
        self.assertFalse((self.styles / "cosmic.toml").exists())
        self.assertFalse((self.styles / "cosmic.jpg").exists())
        self.assertNotIn("cosmic", self.config()["favoriteStyles"])
        self.seed()
        self.assertFalse((self.styles / "cosmic.toml").exists())
        self.assertEqual(json.loads(self.style("restore-deleted").stdout)["keys"], ["cosmic"])
        self.assertTrue((self.styles / "cosmic.toml").exists())

    def test_the_selected_style_cannot_be_deleted_or_hidden(self):
        self.seed()
        result = self.style("delete", "marbled-paper", ok=False)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertTrue((self.styles / "marbled-paper.toml").exists())
        result = self.run_bin(BIN / "astro-arc-config", "--hide-styles", "true", "marbled-paper", ok=False)
        self.assertNotEqual(result.returncode, 0)

    def test_revert_puts_the_seed_back_and_keeps_an_undo(self):
        self.seed()
        path = self.styles / "ukiyoe.toml"
        path.write_text(path.read_text() + "# tweaked\n")
        self.assertTrue(json.loads(self.style("revert", "ukiyoe").stdout)["ok"])
        self.assertEqual(path.read_bytes(), (SEED_DIR / "ukiyoe.toml").read_bytes())
        self.style("tweak-undo", path)
        self.assertTrue(path.read_text().endswith("# tweaked\n"))

    def test_a_renamed_seed_takes_the_selection_with_it(self):
        self.run_bin(BIN / "astro-arc-config", "--set-art-style", "ghibli")
        self.run_bin(BIN / "astro-arc-config", "--favorite-style", "ghibli", "true")
        self.seed()
        config = self.config()
        self.assertEqual(config["artStyle"], "cel-pastoral")
        self.assertEqual(config["favoriteStyles"], ["cel-pastoral"])
        self.assertNotIn("cel-pastoral", config["hiddenStyles"])

    def test_every_starter_key_is_a_seed(self):
        seeds = {p.stem for p in SEED_DIR.glob("*.toml")}
        self.assertLessEqual(VISIBLE, seeds)
        self.assertLessEqual(set(STARTER["renamed"].values()), seeds)
        for key in seeds:
            self.assertTrue((SEED_DIR / f"{key}.jpg").is_file(), key)


if __name__ == "__main__":
    unittest.main()
