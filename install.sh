#!/bin/bash
# Astro-Arc installer.
#
# THE REPOSITORY IS THE PLUGIN. Clone it straight into Omarchy's plugins
# directory and run this from there:
#
#   git clone https://github.com/Gmacadoches/astro-arc.git \
#     ~/.config/omarchy/plugins/astro-arc
#   ~/.config/omarchy/plugins/astro-arc/install.sh
#
# That is the same shape every other Omarchy plugin uses, and it means nothing
# here is symlinked or copied into place: manifest.json sits where the plugin
# registry looks for it, and the backend runs from backend/bin beside it. So
# `git pull` is the entire update story, and an edit to a pipeline TOML takes
# effect on the next generation — in a git working tree, where a later pull
# shows you a conflict instead of silently overwriting your tuning.
#
# Until 2026-09-13 this script symlinked plugin/ and backend/ from a checkout
# somewhere else (~/Projects/astro-arc by convention). That worked, but it made
# the clone's location load-bearing and undocumented: move or delete it and the
# widget vanished from the bar with no error at all, because the symlink
# dangled. Nothing is load-bearing now except the directory you cloned into.
#
# What this script actually does — all of it OUTSIDE the repo, because none of
# it can live in a git checkout:
#   ~/.local/share/omarchy/astro-arc/venv      the Python venv + dependencies
#   ~/.config/systemd/user/astro-arc-*         the periodic-generation timer
#   ~/.local/share/applications/...desktop     the astroarc:// URI handler
#
# Flags:
#   --dev        symlink this checkout into the plugins directory instead, for
#                working on Astro-Arc from a repo you keep somewhere else
#   --no-venv    skip venv creation (you will have to make it yourself)
#   --uninstall  remove everything above. Never touches the repo, and never
#                touches your themes, history or config under ~/.local/state.
#
# Safe to re-run at any time.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$REPO_DIR/backend/bin"
PLUGIN_TARGET="$HOME/.config/omarchy/plugins/astro-arc"
VENV_DIR="$HOME/.local/share/omarchy/astro-arc/venv"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
DESKTOP_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$DESKTOP_DIR/astro-arc-save-theme-handler.desktop"

DEV_MODE=0
MAKE_VENV=1
UNINSTALL=0
for arg in "$@"; do
  case "$arg" in
    --dev) DEV_MODE=1 ;;
    --no-venv) MAKE_VENV=0 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help) sed -n '2,39p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "install.sh: unknown flag '$arg' (try --help)" >&2; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------

if [[ $UNINSTALL == 1 ]]; then
  echo "Removing what install.sh created. Your repo, themes, history and"
  echo "settings are NOT touched."
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now astro-arc-generate.timer >/dev/null 2>&1 || true
  fi
  rm -fv "$SYSTEMD_USER_DIR/astro-arc-generate.service" \
         "$SYSTEMD_USER_DIR/astro-arc-generate.timer" "$DESKTOP_FILE" 2>/dev/null || true
  [[ -L $PLUGIN_TARGET ]] && rm -v "$PLUGIN_TARGET"
  echo
  echo "Left in place on purpose:"
  echo "  $VENV_DIR (delete it yourself if you want the disk back)"
  echo "  $HOME/.local/state/omarchy/astro-arc (your generations and themes)"
  echo "  $REPO_DIR (this checkout — delete it to finish removing Astro-Arc)"
  exit 0
fi

# ---- 1. the plugin itself -------------------------------------------------
# Nothing to install in the normal case: the registry reads manifest.json from
# wherever this checkout sits, so if that is already the plugins directory we
# are done before we start.

if [[ $DEV_MODE == 1 ]]; then
  if [[ -L $PLUGIN_TARGET ]]; then
    rm "$PLUGIN_TARGET"
  elif [[ -e $PLUGIN_TARGET ]]; then
    backup="${PLUGIN_TARGET}.pre-dev-$(date +%Y%m%d%H%M%S)"
    echo "BACKUP  $PLUGIN_TARGET -> $backup"
    mv "$PLUGIN_TARGET" "$backup"
  fi
  mkdir -p "$(dirname "$PLUGIN_TARGET")"
  ln -s "$REPO_DIR" "$PLUGIN_TARGET"
  echo "LINKED  $PLUGIN_TARGET -> $REPO_DIR  (--dev)"
elif [[ "$(readlink -f "$PLUGIN_TARGET" 2>/dev/null)" == "$REPO_DIR" ]]; then
  echo "OK      plugin is installed at $PLUGIN_TARGET"
else
  echo "NOTE    this checkout is not in Omarchy's plugins directory."
  echo "        The backend below will still be set up, but the bar widget will"
  echo "        not appear until the plugin is somewhere the shell looks:"
  echo
  echo "          git clone <url> $PLUGIN_TARGET"
  echo
  echo "        or, to keep developing from here:  ./install.sh --dev"
fi

# ---- 1b. clean up the pre-2026-09-13 layout -------------------------------
# That install symlinked backend/bin and backend/pipeline to fixed paths under
# ~/.local/share/omarchy/astro-arc. Nothing reads them now — the code runs from
# the checkout — so they are stale pointers that will confuse the next person
# to go looking. Only ever removes SYMLINKS: venv/, models/ and logs/ live in
# that same directory, are real, generated, and must survive.
for stale in bin pipeline; do
  stale_path="$HOME/.local/share/omarchy/astro-arc/$stale"
  if [[ -L $stale_path ]]; then
    rm "$stale_path"
    echo "REMOVED $stale_path (stale symlink from the old layout)"
  fi
done

# ---- 2. the Python venv ---------------------------------------------------
# Outside the repo deliberately: it is generated, it is large, and it must
# never show up in a git status.

if [[ $MAKE_VENV == 0 ]]; then
  echo "SKIPPED venv (--no-venv)"
elif [[ -x "$VENV_DIR/bin/python3" ]]; then
  echo "OK      venv already exists at $VENV_DIR"
else
  echo "CREATE  $VENV_DIR"
  if python3 -m venv "$VENV_DIR" \
     && "$VENV_DIR/bin/pip" install --quiet --upgrade pip \
     && "$VENV_DIR/bin/pip" install --quiet pyswisseph timezonefinder pillow; then
    echo "OK      venv ready (pyswisseph, timezonefinder, pillow)"
  else
    echo "FAILED  could not build the venv. Astro-Arc cannot generate without it." >&2
    echo "        Try it by hand to see the error:" >&2
    echo "          python3 -m venv \"$VENV_DIR\"" >&2
    echo "          \"$VENV_DIR/bin/pip\" install pyswisseph timezonefinder pillow" >&2
  fi
fi

# ---- 3. the periodic-generation timer -------------------------------------
# Both units are GENERATED rather than linked: the service's ExecStart is an
# absolute path into this checkout, which differs per machine.

mkdir -p "$SYSTEMD_USER_DIR"
# rm -f first, every time. Installs made before 2026-09-13 SYMLINKED these unit
# files back into the repo, and writing through an inherited symlink does not
# replace it — `>` follows it and truncates its target, so upgrading used to
# write a generated unit straight into the checkout (and `cp` refused outright,
# with "are the same file", aborting the rest of the install under `set -e`).
rm -f "$SYSTEMD_USER_DIR/astro-arc-generate.service" \
      "$SYSTEMD_USER_DIR/astro-arc-generate.timer"
sed "s|__ASTRO_ARC_BIN__|$BIN_DIR|g" \
  "$REPO_DIR/backend/systemd/astro-arc-generate.service.tpl" \
  >"$SYSTEMD_USER_DIR/astro-arc-generate.service"
cp "$REPO_DIR/backend/systemd/astro-arc-generate.timer" "$SYSTEMD_USER_DIR/"
echo "WROTE   $SYSTEMD_USER_DIR/astro-arc-generate.{service,timer}"

# `command -v systemctl` is not enough: systemctl can be installed and still
# have no user manager to talk to — over SSH with no lingering session, inside
# a container, or before the graphical session comes up. Under `set -e` that
# used to take this script down half done. The timer is a backstop, not a
# requirement — the widget polls on its own — so this must never be fatal.
if ! command -v systemctl >/dev/null 2>&1; then
  echo "SKIPPED timer enable — systemctl not found"
elif systemctl --user daemon-reload >/dev/null 2>&1 \
     && systemctl --user enable --now astro-arc-generate.timer >/dev/null 2>&1; then
  echo "ENABLED astro-arc-generate.timer"
else
  echo "SKIPPED timer enable — no user systemd session reachable."
  echo "        Re-run from a normal desktop session, or enable it by hand:"
  echo "          systemctl --user enable --now astro-arc-generate.timer"
  echo "        Astro-Arc still works without it: the widget polls on its own."
fi

# ---- 4. the astroarc:// handler -------------------------------------------
# Generated, not linked: a desktop entry's Exec= needs a real absolute path.

mkdir -p "$DESKTOP_DIR"
rm -f "$DESKTOP_FILE"   # same reason as the unit files above
sed "s|__ASTRO_ARC_BIN__|$BIN_DIR|g" \
  "$REPO_DIR/backend/astro-arc-save-theme-handler.desktop.tpl" >"$DESKTOP_FILE"
echo "WROTE   $DESKTOP_FILE"
command -v update-desktop-database >/dev/null 2>&1 \
  && update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
command -v xdg-mime >/dev/null 2>&1 \
  && xdg-mime default astro-arc-save-theme-handler.desktop x-scheme-handler/astroarc 2>/dev/null || true

# ---------------------------------------------------------------------------

cat <<EOF

Installed. Two things left, both from the widget itself:

  1. Open the Astro-Arc widget in the bar, paste an OpenAI API key, and enter
     your birth date, time and place.
  2. Pick a Schedule. It starts at None, so nothing generates — and nothing is
     charged — until you press Regenerate or choose a cadence.

  Reload the shell to see the widget:  omarchy-restart-shell

To update:     git -C "$REPO_DIR" pull
To uninstall:  "$REPO_DIR/install.sh" --uninstall
EOF
