"""astro-arc-motion downloads only when asked, and says exactly what it will
download: the pinned packages in the script are the ones plan prints and the
README lists, and making a depth map never fetches the model."""
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOTION = ROOT / "backend" / "bin" / "astro-arc-motion"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from throwaway import throwaway_home  # noqa: E402


def pinned_packages():
    match = re.search(r"^PACKAGES=\(([^)]*)\)", MOTION.read_text(), re.M)
    return [tuple(p.split("==")) for p in match.group(1).split()]


class MotionTest(unittest.TestCase):
    def setUp(self):
        self.home, self.env = throwaway_home(self)

    def motion(self, *args):
        return subprocess.run([MOTION, *args], env=self.env, capture_output=True, text=True)

    def test_every_package_is_pinned(self):
        packages = pinned_packages()
        self.assertTrue(packages)
        for name, version in packages:
            self.assertRegex(version, r"^\d+(\.\d+)+$", name)

    def test_plan_lists_the_pinned_packages_and_the_model(self):
        lines = self.motion("plan", "--venv").stdout.splitlines()
        self.assertEqual([tuple(l.split("\t")[1:]) for l in lines if l.startswith("package\t")],
                         pinned_packages())
        self.assertEqual(sum(l.startswith("model\t") for l in lines), 1)

    def test_the_readme_names_each_pinned_version(self):
        readme = (ROOT / "README.md").read_text()
        for name, version in pinned_packages():
            self.assertIn(f"{name} {version}", readme)

    def test_a_depth_map_never_downloads(self):
        with tempfile.NamedTemporaryFile(suffix=".png") as image:
            run = self.motion("depth", image.name)
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse((self.home / ".local/state/omarchy/astro-arc/motion").exists())

    def test_only_setup_calls_fetch_model(self):
        text = MOTION.read_text()
        calls = [m.start() for m in re.finditer(r"^\s*fetch_model\s*$", text, re.M)]
        setup = re.search(r"^  setup\)\n(?:.*\n)*?    ;;", text, re.M)
        self.assertTrue(calls)
        for at in calls:
            self.assertTrue(setup.start() < at < setup.end(), "fetch_model outside setup")


if __name__ == "__main__":
    unittest.main()
