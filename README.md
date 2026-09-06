# Astro-Arc

An Omarchy bar-widget plugin that generates a personalized desktop theme —
a background image plus a matching accent-color palette — from your natal
astrology chart and current transits, on a daily/weekly/monthly cadence you
pick. A two-stage LLM pipeline turns the chart into a psychological reading
and then into a single dream-logic image prompt; the rendered image's
palette gets applied as a live Omarchy theme via `omarchy-theme-set`. Every
real generation is logged to a browsable local HTML review history.

See [`CONTEXT.md`](CONTEXT.md) for the full architecture, every script's
role, conventions, and current implementation status — this file only
covers getting it installed and running.

## Requirements

- **Omarchy** (Hyprland + Quickshell) — this is a Quickshell bar-widget
  plugin; it won't run standalone.
- **Python 3.11+** (uses stdlib `tomllib`; developed against 3.14).
- **System tools**: `jq`, `secret-tool` (from `libsecret`), `hyprctl`
  (ships with Hyprland) — all three are already part of a standard
  Omarchy install.
- **An OpenAI API key** — required regardless of image backend, since
  Stage 1 (interpretation) and Stage 2 (image prompt) are both chat calls.
  The "local" image backend only makes the *image render* free; get one at
  <https://platform.openai.com/api-keys>.

## Install

```sh
git clone https://github.com/Gmacadoches/astro-arc.git ~/Projects/astro-arc
cd ~/Projects/astro-arc
./install.sh
```

This symlinks `plugin/` and `backend/{bin,pipeline}` into the paths Omarchy
expects (`~/.config/omarchy/plugins/astro-arc` and
`~/.local/share/omarchy/astro-arc/{bin,pipeline}`) — editing a file in the
repo takes effect immediately, no reinstall step. Safe to re-run any time.
Anything real already at a target path is backed up, never deleted.

### Python environment

```sh
python3 -m venv ~/.local/share/omarchy/astro-arc/venv
~/.local/share/omarchy/astro-arc/venv/bin/pip install \
  pyswisseph timezonefinder pillow
```

Only if you'll use the local (free, CPU-only) image backend instead of
OpenAI's, also install the SD stack (large download; its checkpoint is
fetched automatically on first use):

```sh
~/.local/share/omarchy/astro-arc/venv/bin/pip install torch diffusers
```

### Configure birth data and location

```sh
~/.local/share/omarchy/astro-arc/bin/astro-arc-config \
  --set-birth YYYY-MM-DD HH:MM
~/.local/share/omarchy/astro-arc/bin/astro-arc-config \
  --set-location "City Name" [lat,lon]
```

(Or set these from the widget's own settings panel instead — see below.)

### Store an API key

There's no CLI "store" command by design — keys go straight into the
system keyring via `secret-tool`, never a file. Astro-Arc uses three
independent slots (`stage1`/`stage2`/`image`, see `CONTEXT.md`), so at
minimum store one for `stage1` and `stage2` (they can be the same key):

```sh
echo -n "sk-..." | secret-tool store --label="Astro-Arc stage1 key" \
  service astro-arc account openai-api-key-stage1
echo -n "sk-..." | secret-tool store --label="Astro-Arc stage2 key" \
  service astro-arc account openai-api-key-stage2
# Only if using the OpenAI image backend (default):
echo -n "sk-..." | secret-tool store --label="Astro-Arc image key" \
  service astro-arc account openai-api-key
```

Easier: skip this and use the widget's settings panel (gear icon) instead
— it does the same `secret-tool` write for you, with validation.

### Reload and run

```sh
omarchy-restart-shell
```

Click the ✦ icon in the bar, open settings (⚙) to confirm your birth data
and keys look right, then hit Regenerate.

## Repo layout

```
astro-arc/
├── install.sh              symlink installer, see above
├── CONTEXT.md               full architecture + conventions
├── CHANGELOG.md              dated history of what changed and why
├── plugin/                  Quickshell plugin (QML UI + manifest)
│   ├── BarWidget.qml, Panel.qml, Model.js, manifest.json
└── backend/                  everything the pipeline runs on
    ├── bin/                   astro-arc-generate and all pipeline scripts
    └── pipeline/               registers.toml / cliches.toml / styles.toml
```
