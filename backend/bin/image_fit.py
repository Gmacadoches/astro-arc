#!/usr/bin/env python3
"""Fits a generated image to an exact target size ("cover" scaling + center
crop, like CSS `object-fit: cover`), shared by both image backends.

Neither backend can just be asked to render at an arbitrary desktop
resolution: sd-turbo (image_gen.py) is CPU-bound and trained around ~512px,
and OpenAI's image models only accept a small fixed set of sizes. Both
generate at whichever size actually suits them, then this crops/scales the
result to the exact background size the user configured (or auto-detected),
so the final file always matches the screen regardless of what the model
itself could produce.
"""

from PIL import Image


def fit_cover(image, target_width, target_height):
    """Resize `image` (a PIL Image) to exactly target_width x target_height,
    scaling to cover the target box and cropping the overflow from the
    center — never distorts aspect ratio, never letterboxes."""
    src_w, src_h = image.size
    scale = max(target_width / src_w, target_height / src_h)
    scaled_w, scaled_h = round(src_w * scale), round(src_h * scale)
    resized = image.resize((scaled_w, scaled_h), Image.LANCZOS)

    left = (scaled_w - target_width) // 2
    top = (scaled_h - target_height) // 2
    return resized.crop((left, top, left + target_width, top + target_height))


def closest_supported_size(target_width, target_height, supported_sizes):
    """Pick whichever of `supported_sizes` ("WxH" strings) best matches the
    target's aspect ratio — used where a backend can only render at a few
    fixed sizes (OpenAI's image API) rather than any size sd-turbo can."""
    target_ratio = target_width / target_height

    def ratio_distance(size_str):
        w, h = (int(n) for n in size_str.split("x"))
        return abs((w / h) - target_ratio)

    return min(supported_sizes, key=ratio_distance)
