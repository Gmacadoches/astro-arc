#!/usr/bin/env python3
"""Every image operation Astro-Arc performs, done by ImageMagick.

OpenAI's image models only accept a small fixed set of sizes, so a render is
made at whichever of those best matches the screen's aspect ratio and then
fitted to the exact background size the user configured (or auto-detected):
scaled to cover it and cropped from the center, like CSS `object-fit: cover`.

This used to be Pillow, which was one of the three packages that made a Python
venv necessary at all. ImageMagick is in Omarchy's base package list, so the
same work now needs nothing installed — and the scripts run on the system's
own python3.
"""

import shutil
import subprocess


class ImageError(Exception):
    pass


def _magick():
    """ImageMagick 7's `magick`, or 6's `convert` on a system that has only
    the older one. Omarchy ships 7."""
    for name in ("magick", "convert"):
        path = shutil.which(name)
        if path:
            return path
    raise ImageError("ImageMagick is not installed (need `magick`). "
                     "On Omarchy: sudo pacman -S imagemagick")


def run_magick(args):
    """Run ImageMagick with `args` and return stdout as text. Raises ImageError
    with ImageMagick's own message on failure, never a bare traceback."""
    proc = subprocess.run([_magick(), *map(str, args)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise ImageError(f"ImageMagick failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def fit_cover(src_path, dst_path, target_width, target_height):
    """Write `src_path` to `dst_path` at exactly target_width x target_height:
    scaled to cover the box (the `^` flag), then the overflow cropped from the
    center. Never distorts the aspect ratio, never letterboxes. `+repage`
    drops the crop offset a PNG would otherwise carry."""
    size = f"{int(target_width)}x{int(target_height)}"
    run_magick([src_path, "-resize", f"{size}^", "-gravity", "center",
                "-extent", size, "+repage", dst_path])


def thumbnail_jpeg(src_path, dst_path, max_px, quality=86):
    """A progressive JPEG no larger than max_px on its longest side (the `>`
    flag never enlarges), stripped of metadata."""
    run_magick([src_path, "-resize", f"{int(max_px)}x{int(max_px)}>", "-strip",
                "-interlace", "JPEG", "-quality", str(int(quality)), dst_path])


def closest_supported_size(target_width, target_height, supported_sizes):
    """Pick whichever of `supported_sizes` ("WxH" strings) best matches the
    target's aspect ratio — the OpenAI image API renders only at a few fixed
    sizes, and fit_cover() takes it from there."""
    target_ratio = target_width / target_height

    def ratio_distance(size_str):
        w, h = (int(n) for n in size_str.split("x"))
        return abs((w / h) - target_ratio)

    return min(supported_sizes, key=ratio_distance)
