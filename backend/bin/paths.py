"""Where Astro-Arc keeps things — the Python twin of astro-arc-paths.sh, which
explains the split. Change a location in both, and nowhere else."""

import os
from pathlib import Path

ARCHIVE_DIR = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "astro-arc"
GENERATIONS_DIR = ARCHIVE_DIR / "generations"
GENERATIONS_INDEX = ARCHIVE_DIR / "generations.json"
GALLERY_FILE = ARCHIVE_DIR / "index.html"

STATE_DIR = Path.home() / ".local/state/omarchy/astro-arc"
CONFIG_FILE = Path.home() / ".local/state/omarchy/settings/astro-arc.json"
