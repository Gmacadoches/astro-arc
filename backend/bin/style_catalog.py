#!/usr/bin/env python3
"""The Style dropdown's source: every installed theme, read from the theme
files themselves (decisions 4 and 5), the way model_catalog.py is the source
for the model dropdowns.

    style_catalog.py                      JSON array, one row per theme
    style_catalog.py --set-preview KEY IMAGE
                                          IMAGE becomes the theme KEY's
                                          preview (<key>.jpg beside its .toml)
    style_catalog.py --change-preview KEY same, choosing IMAGE in a file dialog

Listing seeds first (style_seeds.py), so a release's new styles are there.
Each row is {key, label, valid, errors, warnings, preview, path, starter,
changed, isNew}: `starter` for a style that came from a seed, `changed` when
its files are no longer that seed's (a tweak, a new preview), `isNew` for one
a release added that the Styles view has not shown yet. A theme that fails
validate_charter is still listed, with valid false and its errors, so the
panel can show what is wrong with it instead of the theme silently vanishing.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm_pipeline as lp  # noqa: E402
import style_seeds  # noqa: E402
from image_fit import ImageError, thumbnail_jpeg  # noqa: E402

# Matches the shipped previews (docs/images/styles/, 1200x675).
PREVIEW_PX = 1200


def catalog():
    # Quietly: the panel shows this script's stderr as a warning.
    style_seeds.seed_or_warn(report=False)
    seeds = style_seeds.Seeds()
    rows = []
    for key, theme in lp.Catalog.load().styles.items():
        rows.append({
            "key": key,
            "label": theme.label,
            "valid": theme.valid,
            "errors": theme.errors,
            "warnings": theme.warnings,
            "preview": str(theme.preview) if theme.preview.is_file() else "",
            "path": str(theme.path),
            "starter": seeds.is_starter(key),
            "changed": seeds.is_starter(key) and seeds.changed(key),
            "isNew": key in seeds.new,
        })
    return rows


class PreviewError(Exception):
    pass


def set_preview(key, image):
    theme = lp.Catalog.load().styles.get(key)
    if theme is None:
        raise PreviewError(f"No theme '{key}'.")
    image = Path(image)
    if not image.is_file():
        raise PreviewError(f"{image} is not a file.")
    # Written beside the old preview and moved over it, so a failed
    # conversion never leaves the theme without one.
    tmp = theme.preview.with_name(f".{theme.preview.name}.tmp.jpg")
    try:
        thumbnail_jpeg(image, tmp, PREVIEW_PX)
        tmp.replace(theme.preview)
    except ImageError as e:
        raise PreviewError(str(e)) from e
    finally:
        tmp.unlink(missing_ok=True)
    return theme.preview


def choose_image():
    """An image path from a zenity file dialog, or None if it was cancelled."""
    if not shutil.which("zenity"):
        raise PreviewError("Choosing a file needs zenity, which is not installed.")
    proc = subprocess.run(
        ["zenity", "--file-selection", "--title=Choose a preview image",
         "--file-filter=Images | *.jpg *.jpeg *.png *.webp", "--file-filter=All files | *"],
        capture_output=True, text=True)
    # zenity exits 1 when the dialog is closed or cancelled.
    if proc.returncode == 1:
        return None
    if proc.returncode != 0:
        raise PreviewError(f"Could not open the file dialog (zenity exited {proc.returncode}).")
    return proc.stdout.strip() or None


def main():
    args = sys.argv[1:]
    try:
        if args[:1] == ["--set-preview"] and len(args) == 3:
            print(json.dumps({"ok": True, "preview": str(set_preview(args[1], args[2]))}))
        elif args[:1] == ["--change-preview"] and len(args) == 2:
            image = choose_image()
            if image is None:
                print(json.dumps({"ok": True, "cancelled": True}))
            else:
                print(json.dumps({"ok": True, "preview": str(set_preview(args[1], image))}))
        elif not args:
            print(json.dumps(catalog(), indent=2))
        else:
            print(__doc__, file=sys.stderr)
            sys.exit(2)
    except PreviewError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
