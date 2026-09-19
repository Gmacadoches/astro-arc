# Where Astro-Arc keeps things. Sourced by the backend's shell scripts; paths.py
# is the same for Python. Change a location here and nowhere else.
#
# The split follows the XDG base directory spec, and it matters:
#
#   ARCHIVE_DIR  ~/.local/share/astro-arc — YOUR DATA. Every generation's page,
#                wallpaper and theme, and the gallery that lists them. Plain
#                files in your home directory: removing the plugin leaves them
#                exactly where they are.
#   STATE_DIR    ~/.local/state/omarchy/astro-arc — disposable. Caches, cost
#                logs, the rotation memory, the last run. Deleting it costs a
#                few cents of re-computed readings and nothing else.
#   CONFIG_FILE  the one settings file, where Omarchy keeps every plugin's.
#
# Before 2026-09-19 the archive lived in STATE_DIR/reviews, where tools that
# treat state as disposable could clear it. astro-arc-migrate moves it.

ARCHIVE_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/astro-arc"
GENERATIONS_DIR="$ARCHIVE_DIR/generations"
GENERATIONS_INDEX="$ARCHIVE_DIR/generations.json"
GALLERY_FILE="$ARCHIVE_DIR/index.html"

STATE_DIR="$HOME/.local/state/omarchy/astro-arc"
CONFIG_FILE="$HOME/.local/state/omarchy/settings/astro-arc.json"
