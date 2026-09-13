# Backstop scheduler for Astro-Arc generation — fired periodically by
# astro-arc-generate.timer regardless of whether the Omarchy bar/shell is
# even running (the widget's own Panel.qml Timer is the other, redundant
# trigger — see its "Automatic scheduling" comment). `--if-due` makes this
# a no-op unless the configured frequency's current period doesn't already
# have a generation, so firing this on a plain interval never wastes a
# paid image render on a period that's already covered.
#
# Templated, not symlinked: since 2026-09-13 the backend lives inside the
# plugin checkout rather than at a fixed path under ~/.local/share, so the
# absolute path depends on where the repo was cloned. install.sh substitutes
# __ASTRO_ARC_BIN__ and writes the result into ~/.config/systemd/user/ —
# the same treatment the astroarc:// desktop entry already gets, and for the
# same reason.
[Unit]
Description=Astro-Arc scheduled theme generation (no-op unless due)

[Service]
Type=oneshot
ExecStart=__ASTRO_ARC_BIN__/astro-arc-generate --if-due
