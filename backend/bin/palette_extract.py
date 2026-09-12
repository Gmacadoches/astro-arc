#!/usr/bin/env python3
"""Derives an Omarchy colors.toml from a generated background image, so the
theme's accent/background colors actually match the art instead of an
unrelated placeholder. Deliberately simple (quantize to a handful of
dominant colors, pick accent/background/foreground by lightness) rather
than a full palette-extraction pipeline — Phase 4 ("theme assembly") can
replace this with something more refined once the art style itself is
settled; a real photo deserves better than the old hash-based placeholder
colors, but doesn't need k-means clustering to prove that out.

Usage: palette_extract.py <image_path> <colors_toml_output_path>
"""

import colorsys
import sys
from pathlib import Path

from PIL import Image

# GNOME/Nautilus (the file manager) doesn't read colors.toml at all — Omarchy's
# omarchy-theme-set-gnome reads a sibling icons.theme file naming one of these
# fixed Yaru icon-color variants, falling back to a hardcoded "Yaru-blue" when
# that file is absent. Every official Omarchy theme ships one; this project's
# generated theme didn't, which is why the file manager never re-themed.
#
# Hues below are sampled directly from each variant's own folder icon
# (48x48/places/folder.png, most-saturated pixel), not guessed from the name —
# see the astro-arc project's CHANGELOG for the sampling method. Matching by
# nearest hue on a circle, rather than fixed ranges, means no boundary case is
# ever left unhandled.
YARU_ACCENT_HUES = {
    "wartybrown": 29.0,
    "yellow": 37.6,
    "olive": 85.3,
    "sage": 130.6,
    "prussiangreen": 179.1,
    "blue": 210.0,
    "purple": 254.5,
    "magenta": 298.7,
    "red": 349.7,
}
# Below this saturation the extracted accent is too close to grey for any hue
# match to mean anything (an artifact of the accent-selection heuristic below
# picking the "most saturated" of an otherwise muted palette) — the plain,
# uncolored base icon set reads better than a arbitrary near-grey hue guess.
YARU_MIN_SATURATION = 0.12


def nearest_yaru_variant(rgb):
    r, g, b = (c / 255 for c in rgb)
    h, s, _v = colorsys.rgb_to_hsv(r, g, b)
    if s < YARU_MIN_SATURATION:
        return "Yaru"
    hue_deg = h * 360

    def circular_distance(a, b):
        d = abs(a - b) % 360
        return min(d, 360 - d)

    name = min(YARU_ACCENT_HUES, key=lambda n: circular_distance(hue_deg, YARU_ACCENT_HUES[n]))
    return f"Yaru-{name}"

TEMPLATE = """mode = "{mode}"

accent = "{accent}"
selection = "{selection}"
muted = "{muted}"

background = "{background}"
dark_background = "{dark_background}"
darker_background = "{darker_background}"
lighter_background = "{lighter_background}"

foreground = "{foreground}"
dark_foreground = "{dark_foreground}"
light_foreground = "{light_foreground}"
bright_foreground = "{foreground}"

red = "{red}"
yellow = "{yellow}"
orange = "{orange}"
green = "{green}"
cyan = "{cyan}"
blue = "{accent}"
magenta = "{magenta}"
brown = "{brown}"

bright_red = "{red}"
bright_yellow = "{yellow}"
bright_green = "{green}"
bright_cyan = "{cyan}"
bright_blue = "{accent}"
bright_magenta = "{magenta}"
"""

# Every one of these renders as actual text somewhere in the ~17 themed app
# configs (omarchy-theme-color resolves dark_foreground/muted to the ANSI
# "bright black" slot used for comments/dim text, light_foreground to
# "bright white", selection's paired text to bright_foreground) — they used
# to be these fixed Catppuccin-Mocha-ish constants regardless of what
# background the extracted image produced, which is exactly why contrast
# could fail: a constant tuned for one reference background (#1e1e2e-ish)
# has no guaranteed relationship to an arbitrary painted image's darkest/
# lightest color. Added 2026-09-07 (see CHANGELOG.md) — kept as the base hue
# each slot should still read as (an editor's "red" should stay reddish),
# adjusted per-generation for contrast by _ensure_contrast() below rather
# than used verbatim.
BASE_COLORS = {
    "red": (0xf3, 0x8b, 0xa8), "yellow": (0xf9, 0xe2, 0xaf), "orange": (0xf6, 0xb6, 0xab),
    "green": (0xa6, 0xe3, 0xa1), "cyan": (0x94, 0xe2, 0xd5), "magenta": (0xf5, 0xc2, 0xe7),
    "brown": (0x7b, 0x5b, 0x55), "muted": (0x58, 0x5b, 0x70),
    "dark_foreground": (0x6c, 0x70, 0x86), "light_foreground": (0xba, 0xc2, 0xde),
    "selection": (0x45, 0x47, 0x5a),
}
# muted/dark_foreground are meant to read as de-emphasized (comments, dim
# text) — WCAG's 3:1 "UI component" floor keeps them legible without
# making them as prominent as primary text, which 4.5:1 would.
DIM_CONTRAST_RATIO = 3.0
TEXT_CONTRAST_RATIO = 4.5


def _hex(rgb):
    return "#%02x%02x%02x" % rgb


def _lightness(rgb):
    r, g, b = rgb
    return (max(r, g, b) + min(r, g, b)) / 2


def _scale(rgb, factor):
    return tuple(max(0, min(255, round(c * factor))) for c in rgb)


def _relative_luminance(rgb):
    """WCAG relative luminance — the basis for _contrast_ratio below."""
    def lin(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast_ratio(rgb1, rgb2):
    """WCAG contrast ratio, 1 (identical) to 21 (black on white)."""
    l1, l2 = _relative_luminance(rgb1), _relative_luminance(rgb2)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _mix(rgb, target, t):
    return tuple(round(c * (1 - t) + tc * t) for c, tc in zip(rgb, target))


def _ensure_contrast(rgb, reference_rgb, min_ratio):
    """Tint/shade `rgb` toward black or white — whichever actually
    increases contrast against `reference_rgb` — until it clears
    min_ratio, by binary-searching the smallest blend that works. A
    straight RGB blend keeps the result recognizably the same hue (a red
    pushed toward white reads as pink, toward black as maroon — still
    red), which matters for the ANSI slots: an editor's "green" string
    color should stay greenish, not hue-shift into something else.
    Returns `rgb` unchanged if it already clears the ratio."""
    if _contrast_ratio(rgb, reference_rgb) >= min_ratio:
        return rgb
    white_reach = _contrast_ratio((255, 255, 255), reference_rgb)
    black_reach = _contrast_ratio((0, 0, 0), reference_rgb)
    target = (255, 255, 255) if white_reach >= black_reach else (0, 0, 0)
    lo, hi = 0.0, 1.0
    best = target  # if even the full extreme can't reach min_ratio, it's the closest we can get
    for _ in range(20):
        mid = (lo + hi) / 2
        candidate = _mix(rgb, target, mid)
        if _contrast_ratio(candidate, reference_rgb) >= min_ratio:
            best = candidate
            hi = mid
        else:
            lo = mid
    return best


def dominant_colors(image_path, count=5):
    img = Image.open(image_path).convert("RGB")
    img = img.resize((150, 150))  # quantize on a small copy — plenty for a palette, fast
    quantized = img.quantize(colors=count, method=Image.MEDIANCUT)
    palette = quantized.getpalette()[: count * 3]
    counts = sorted(quantized.getcolors(), reverse=True)  # [(pixelCount, paletteIndex), ...]
    colors = []
    for _n, idx in counts:
        r, g, b = palette[idx * 3: idx * 3 + 3]
        colors.append((r, g, b))
    return colors


def build_colors_toml(image_path):
    colors = dominant_colors(image_path)
    by_lightness = sorted(colors, key=_lightness)
    darkest = by_lightness[0]
    lightest = by_lightness[-1]
    # Accent: the most saturated of the dominant colors, so a near-grey
    # background doesn't also become a near-grey accent. Near-black and
    # near-white candidates are excluded first — relative saturation
    # ((hi-lo)/hi) blows up on tiny channel differences in near-black
    # colors, which without this floor kept picking shadow-black "accents"
    # indistinguishable from the background.
    def saturation(rgb):
        r, g, b = rgb
        hi, lo = max(r, g, b), min(r, g, b)
        return 0 if hi == 0 else (hi - lo) / hi

    midtones = [c for c in colors if 40 <= _lightness(c) <= 215]
    accent = max(midtones or colors, key=saturation)

    dark_theme = _lightness(darkest) < 128
    mode = "dark" if dark_theme else "light"
    background = darkest if dark_theme else lightest
    foreground_raw = lightest if dark_theme else darkest

    # accent/foreground come from the image itself, so only their contrast
    # against the derived background is guaranteed here (their hue is
    # whatever the art actually contains). Every other slot below starts
    # from a fixed reference hue (BASE_COLORS) and is adjusted the same
    # way — see _ensure_contrast's docstring for why a straight tint/shade
    # is enough to keep each one recognizably its own color.
    accent_fixed = _ensure_contrast(accent, background, TEXT_CONTRAST_RATIO)
    foreground = _ensure_contrast(foreground_raw, background, TEXT_CONTRAST_RATIO)
    ansi = {
        name: _ensure_contrast(rgb, background, TEXT_CONTRAST_RATIO)
        for name, rgb in BASE_COLORS.items()
        if name not in ("muted", "dark_foreground", "selection")
    }
    muted = _ensure_contrast(BASE_COLORS["muted"], background, DIM_CONTRAST_RATIO)
    dark_foreground = _ensure_contrast(BASE_COLORS["dark_foreground"], background, DIM_CONTRAST_RATIO)
    # selection is a background chip, not text — the thing that needs
    # contrast is whatever text renders on top of it. omarchy-theme-color
    # defaults selection_foreground to bright_foreground (== foreground
    # here), so that's the pairing that actually matters, not `background`.
    selection = _ensure_contrast(BASE_COLORS["selection"], foreground, TEXT_CONTRAST_RATIO)

    toml_text = TEMPLATE.format(
        mode=mode,
        accent=_hex(accent_fixed),
        background=_hex(background),
        dark_background=_hex(_scale(background, 0.85)),
        darker_background=_hex(_scale(background, 0.65)),
        lighter_background=_hex(_scale(background, 1.35)),
        foreground=_hex(foreground),
        muted=_hex(muted),
        dark_foreground=_hex(dark_foreground),
        light_foreground=_hex(ansi["light_foreground"]),
        selection=_hex(selection),
        red=_hex(ansi["red"]), yellow=_hex(ansi["yellow"]), orange=_hex(ansi["orange"]),
        green=_hex(ansi["green"]), cyan=_hex(ansi["cyan"]), magenta=_hex(ansi["magenta"]),
        brown=_hex(ansi["brown"]),
    )
    # The raw (pre-contrast-adjustment) accent is used for icon-hue
    # matching below — it's the image's actual dominant hue; the
    # readability adjustment above is a text-contrast concern, not a
    # "what color is this image" one.
    return toml_text, accent


def main():
    if len(sys.argv) != 3:
        print("Usage: palette_extract.py <image_path> <colors_toml_output_path>", file=sys.stderr)
        sys.exit(1)
    image_path, output_path = sys.argv[1], sys.argv[2]
    toml_text, accent = build_colors_toml(image_path)
    output_path = Path(output_path)
    output_path.write_text(toml_text)
    print(f"Wrote {output_path}", file=sys.stderr)

    # Sibling file, same convention every official Omarchy theme uses — see
    # nearest_yaru_variant's docstring/comment above for why this exists.
    icons_path = output_path.parent / "icons.theme"
    icons_path.write_text(nearest_yaru_variant(accent) + "\n")
    print(f"Wrote {icons_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
