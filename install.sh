#!/bin/bash
# Astro-Arc installer.
#
# Symlinks this repo's files into the locations Omarchy/Quickshell expect,
# so editing a file in the repo takes effect immediately with no separate
# "reinstall" step:
#   plugin/         -> ~/.config/omarchy/plugins/astro-arc
#   backend/bin      -> ~/.local/share/omarchy/astro-arc/bin
#   backend/pipeline -> ~/.local/share/omarchy/astro-arc/pipeline
#
# Safe to re-run any time (idempotent) — an existing correct symlink is
# left alone; anything else in the way (a real file/dir, or a symlink
# pointing somewhere else) is moved aside to a timestamped backup, never
# deleted outright.
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

mkdir -p "$BACKEND_ROOT"

link "$REPO_DIR/plugin" "$PLUGIN_TARGET"
link "$REPO_DIR/backend/bin" "$BIN_TARGET"
link "$REPO_DIR/backend/pipeline" "$PIPELINE_TARGET"

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
