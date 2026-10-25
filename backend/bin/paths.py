"""Where Astro-Arc keeps things — the Python twin of astro-arc-paths.sh, which
explains the split. Panel.qml's property block is the third copy, since QML
cannot import either. Change a location in all three, and nowhere else."""

import os
from pathlib import Path

ARCHIVE_DIR = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "astro-arc"
GENERATIONS_DIR = ARCHIVE_DIR / "generations"
GENERATIONS_INDEX = ARCHIVE_DIR / "generations.json"
GALLERY_FILE = ARCHIVE_DIR / "index.html"

STATE_DIR = Path.home() / ".local/state/omarchy/astro-arc"
PIPELINE_STATE_DIR = STATE_DIR / "pipeline"
VERIFY_DIR = STATE_DIR / "verification"
CONFIG_FILE = Path.home() / ".local/state/omarchy/settings/astro-arc.json"

USER_STYLES_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "astro-arc" / "styles"
OMARCHY_THEMES_DIR = Path.home() / ".config/omarchy/themes"
OMARCHY_CURRENT_DIR = Path.home() / ".local/state/omarchy/current"
