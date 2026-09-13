#!/bin/bash
# Astro-Arc installer.
#
# Symlinks this repo's files into the locations Omarchy/Quickshell expect,
# so editing a file in the repo takes effect immediately with no separate
# "reinstall" step:
#   plugin/           -> ~/.config/omarchy/plugins/astro-arc
#   backend/bin        -> ~/.local/share/omarchy/astro-arc/bin
#   backend/pipeline   -> ~/.local/share/omarchy/astro-arc/pipeline
#   backend/systemd/*  -> ~/.config/systemd/user/ (one symlink per unit —
#                         each needs its own individual name in that
#                         directory, unlike the directory-level symlinks
#                         above, so systemd itself can find them by name)
#
# The systemd units need no per-machine templating (unlike the desktop
# entry below) — systemd's own %h specifier resolves to this user's home
# directory at run time, so they're symlinked as plain files, edits to
# them take effect on the next `daemon-reload` with no regeneration step.
#
# Also generates (not symlinks — it needs $HOME baked in, which a desktop
# entry's Exec= can't itself expand) ~/.local/share/applications/astro-
# arc-save-theme-handler.desktop from backend/astro-arc-save-theme-
# handler.desktop.tpl, and registers it as the astroarc:// URI handler —
# what a review page's Save Theme button actually opens, since a static
# HTML page has no server of its own to run a script from. Same mechanism
# Aether's own aether:// links use on this system (see
# /usr/share/applications/li.oever.aether.url-handler.desktop).
#
# Safe to re-run any time (idempotent) — an existing correct symlink is
# left alone; anything else in the way (a real file/dir, or a symlink
# pointing somewhere else) is moved aside to a timestamped backup, never
# deleted outright. The desktop file is plain regeneration/re-registration
# — nothing there to preserve.
#
# What this script deliberately does NOT do (see CONTEXT.md instead):
#   - create the Python venv or install its dependencies
#   - store or touch any API key (that's astro-arc-apikey's job, backed by
#     the system keyring via secret-tool — never a file)
#   - write any config or runtime state

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PLUGIN_TARGET="$HOME/.config/omarchy/plugins/astro-arc"
BACKEND_ROOT="$HOME/.local/share/omarchy/astro-arc"
BIN_TARGET="$BACKEND_ROOT/bin"
PIPELINE_TARGET="$BACKEND_ROOT/pipeline"

link() {
  local src="$1" dst="$2"
  local resolved_src
  resolved_src="$(cd "$src" && pwd)"

  if [[ -L $dst ]]; then
    if [[ "$(readlink -f "$dst")" == "$resolved_src" ]]; then
      echo "OK      $dst (already linked)"
      return
    fi
    echo "RELINK  $dst (was pointing elsewhere)"
    rm "$dst"
  elif [[ -e $dst ]]; then
    local backup="${dst}.pre-install-backup-$(date +%Y%m%d%H%M%S)"
    echo "BACKUP  $dst -> $backup"
    mv "$dst" "$backup"
  fi

  mkdir -p "$(dirname "$dst")"
  ln -s "$resolved_src" "$dst"
  echo "LINKED  $dst -> $resolved_src"
}

# Same idempotent contract as link() above, but for a single file rather
# than a directory — link()'s `cd "$src" && pwd` resolution assumes a
# directory, which a systemd unit file isn't.
link_file() {
  local src="$1" dst="$2"
  if [[ -L $dst ]]; then
    if [[ "$(readlink -f "$dst")" == "$(readlink -f "$src")" ]]; then
      echo "OK      $dst (already linked)"
      return
    fi
    echo "RELINK  $dst (was pointing elsewhere)"
    rm "$dst"
  elif [[ -e $dst ]]; then
    local backup="${dst}.pre-install-backup-$(date +%Y%m%d%H%M%S)"
    echo "BACKUP  $dst -> $backup"
    mv "$dst" "$backup"
  fi
  mkdir -p "$(dirname "$dst")"
  ln -s "$src" "$dst"
  echo "LINKED  $dst -> $src"
}

mkdir -p "$BACKEND_ROOT"

link "$REPO_DIR/plugin" "$PLUGIN_TARGET"
link "$REPO_DIR/backend/bin" "$BIN_TARGET"
link "$REPO_DIR/backend/pipeline" "$PIPELINE_TARGET"

SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
for unit in "$REPO_DIR"/backend/systemd/*; do
  link_file "$unit" "$SYSTEMD_USER_DIR/$(basename "$unit")"
done

# `command -v systemctl` is not enough: systemctl can be installed and still
# have no user manager to talk to — an install over SSH with no lingering
# session, inside a container, or before the graphical session comes up. Under
# `set -e` that took the whole script down HALF DONE, after the symlinks but
# before the astroarc:// handler and the "what to do next" instructions, with
# nothing but "Failed to connect to user scope bus" to explain it. The timer is
# a backstop, not a requirement — the widget polls on its own — so failing to
# enable it must never be fatal.
if ! command -v systemctl >/dev/null 2>&1; then
  echo "SKIPPED astro-arc-generate.timer enable — systemctl not found"
elif systemctl --user daemon-reload >/dev/null 2>&1 \
     && systemctl --user enable --now astro-arc-generate.timer >/dev/null 2>&1; then
  echo "ENABLED astro-arc-generate.timer (systemctl --user)"
else
  echo "SKIPPED astro-arc-generate.timer enable — no user systemd session reachable."
  echo "        Re-run this script from a normal desktop session, or enable it by hand:"
  echo "          systemctl --user enable --now astro-arc-generate.timer"
  echo "        Astro-Arc still works without it: the widget polls on its own."
fi

DESKTOP_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$DESKTOP_DIR/astro-arc-save-theme-handler.desktop"
mkdir -p "$DESKTOP_DIR"
sed "s|__ASTRO_ARC_BIN__|$BIN_TARGET|g" "$REPO_DIR/backend/astro-arc-save-theme-handler.desktop.tpl" >"$DESKTOP_FILE"
echo "WROTE   $DESKTOP_FILE"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi
if command -v xdg-mime >/dev/null 2>&1; then
  xdg-mime default astro-arc-save-theme-handler.desktop x-scheme-handler/astroarc 2>/dev/null || true
  echo "REGISTERED astroarc:// -> astro-arc-save-theme-handler.desktop"
fi

cat <<'EOF'

Astro-Arc files are placed. Remaining one-time setup (not automated by
this script) — see README.md for the full walkthrough:

  1. Create the Python venv and install its dependencies:
       python3 -m venv ~/.local/share/omarchy/astro-arc/venv
       ~/.local/share/omarchy/astro-arc/venv/bin/pip install \
         pyswisseph timezonefinder pillow
     Only if using the local (free) image backend, also:
       ~/.local/share/omarchy/astro-arc/venv/bin/pip install torch diffusers

  2. Set your birth data and location (astro-arc-config --set-birth /
     --set-location), and store an API key (secret-tool store — see
     README.md for the exact commands, or just use the widget's own
     settings panel instead).

  3. Reload the shell: omarchy-restart-shell

EOF
