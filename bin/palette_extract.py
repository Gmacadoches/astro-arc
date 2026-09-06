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

import sys
from pathlib import Path

from PIL import Image

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

    return TEMPLATE.format(
        mode=mode,
        accent=_hex(accent),
        background=_hex(background),
        dark_background=_hex(_scale(background, 0.85)),
        darker_background=_hex(_scale(background, 0.65)),
        lighter_background=_hex(_scale(background, 1.35)),
        foreground=_hex(foreground),
    )


def main():
    if len(sys.argv) != 3:
        print("Usage: palette_extract.py <image_path> <colors_toml_output_path>", file=sys.stderr)
        sys.exit(1)
    image_path, output_path = sys.argv[1], sys.argv[2]
    toml_text = build_colors_toml(image_path)
    Path(output_path).write_text(toml_text)
    print(f"Wrote {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
