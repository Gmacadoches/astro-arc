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
selection = "#45475a"
muted = "#585b70"

background = "{background}"
dark_background = "{dark_background}"
darker_background = "{darker_background}"
lighter_background = "{lighter_background}"

foreground = "{foreground}"
dark_foreground = "#6c7086"
light_foreground = "#bac2de"
bright_foreground = "{foreground}"

red = "#f38ba8"
yellow = "#f9e2af"
orange = "#f6b6ab"
green = "#a6e3a1"
cyan = "#94e2d5"
blue = "{accent}"
magenta = "#f5c2e7"
brown = "#7b5b55"

bright_red = "#f38ba8"
bright_yellow = "#f9e2af"
bright_green = "#a6e3a1"
bright_cyan = "#94e2d5"
bright_blue = "{accent}"
bright_magenta = "#f5c2e7"
"""


def _hex(rgb):
    return "#%02x%02x%02x" % rgb


def _lightness(rgb):
    r, g, b = rgb
    return (max(r, g, b) + min(r, g, b)) / 2


def _scale(rgb, factor):
    return tuple(max(0, min(255, round(c * factor))) for c in rgb)


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
    foreground = lightest if dark_theme else darkest

    toml_text = TEMPLATE.format(
        mode=mode,
        accent=_hex(accent),
        background=_hex(background),
        dark_background=_hex(_scale(background, 0.85)),
        darker_background=_hex(_scale(background, 0.65)),
        lighter_background=_hex(_scale(background, 1.35)),
        foreground=_hex(foreground),
    )
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
