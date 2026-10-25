"""Every location is spelled in one place: backend/bin/paths.py for Python,
astro-arc-paths.sh for shell, and the property blocks of Panel.qml and
Motion.qml for QML. This fails on code anywhere else that builds a path from
the home directory, so a new harness imports the location instead of copying
it.

    python3 -m unittest discover backend/tests
"""

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "backend" / "bin"

HOME_ANCHORED = re.compile(
    r'Path\.home\(\)|\$HOME\b|\$\{HOME\b|XDG_[A-Z]+_HOME|env\("HOME"\)|\broot\.home\b|\bhome \+')
COMMENT = re.compile(r"^\s*(#|//)")

# The files that define locations, and the lines of Panel.qml and Motion.qml
# that do.
DEFINERS = {BIN / "paths.py", BIN / "astro-arc-paths.sh"}
PANEL_DEFINITION = re.compile(r"readonly property string (home|archiveDir|stateDir|configPath|omarchyCurrentDir):")

# Home-anchored code that names somewhere that is not Astro-Arc's: (file, a
# snippet of the line).
NOT_OURS = {
    # The save dialog's starting folder: the user's Downloads.
    (BIN / "astro-arc-export-theme", 'out_dir="$HOME/Downloads"'),
    (BIN / "astro-arc-export-theme", 'out_dir="$HOME"'),
    (BIN / "astro-arc-share-record", 'start_dir="$HOME/Downloads"'),
    (BIN / "astro-arc-share-record", 'start_dir="$HOME"'),
    # Other agents' skills folders (SKILL_HOMES).
    (BIN / "astro-arc-style", "home = Path.home()"),
}


def code_files():
    for path in sorted(BIN.iterdir()):
        if path.is_file() and path.suffix in ("", ".py", ".sh"):
            yield path
    yield from sorted(ROOT.glob("*.qml"))
    yield from sorted(ROOT.glob("*.js"))
    yield from sorted((ROOT / "sections").glob("*.qml"))


class PathsTest(unittest.TestCase):
    def test_no_location_is_spelled_outside_the_path_modules(self):
        found = []
        for path in code_files():
            if path in DEFINERS:
                continue
            for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                if COMMENT.match(line) or not HOME_ANCHORED.search(line):
                    continue
                if path.name in ("Panel.qml", "Motion.qml") and PANEL_DEFINITION.search(line):
                    continue
                if any(path == where and snippet in line for where, snippet in NOT_OURS):
                    continue
                found.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
        self.assertEqual(found, [], "import the location from paths.py / astro-arc-paths.sh instead")

    def test_shell_and_python_agree(self):
        home = "/nonexistent-home"
        env = {"HOME": home, "PATH": os.environ.get("PATH", "")}
        names = ["ARCHIVE_DIR", "GENERATIONS_DIR", "GENERATIONS_INDEX", "GALLERY_FILE", "STATE_DIR",
                 "PIPELINE_STATE_DIR", "VERIFY_DIR", "CONFIG_FILE", "USER_STYLES_DIR", "OMARCHY_THEMES_DIR", "OMARCHY_CURRENT_DIR"]
        shell = subprocess.run(
            ["bash", "-c", f'source "{BIN}/astro-arc-paths.sh"; '
             + "; ".join(f'echo "${n}"' for n in names)],
            env=env, capture_output=True, text=True, check=True).stdout.split("\n")[:-1]
        python = subprocess.run(
            [sys.executable, "-c", f"import sys; sys.path.insert(0, {str(BIN)!r}); import paths; "
             + "; ".join(f"print(paths.{n})" for n in names)],
            env=env, capture_output=True, text=True, check=True).stdout.split("\n")[:-1]
        self.assertEqual(dict(zip(names, shell)), dict(zip(names, python)))


if __name__ == "__main__":
    unittest.main()
