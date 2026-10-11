#!/usr/bin/env python3
"""Lay renders out as a labelled grid with ImageMagick's `montage`.

Usage:
    contact_sheet.py --out sheet.jpg --cols N  "Row label=img1,img2,..." ...

Each positional argument is one row: a label, then that row's images in
column order (paths, or folders under verification/ and a file name, as
given). A missing image leaves a grey tile, so rows stay aligned. Written for
STYLE-OVERHAUL Prompt 3's before/after sheet.
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

VERIFY_ROOT = Path.home() / ".local/state/omarchy/astro-arc/verification"
TILE = "480x270"


def resolve(p):
    path = Path(p)
    return path if path.is_absolute() or path.exists() else VERIFY_ROOT / p


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cols", type=int, required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("rows", nargs="+")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        blank = Path(tmp) / "blank.png"
        subprocess.run(["magick", "-size", TILE, "xc:#444", str(blank)], check=True)
        cmd = ["montage"]
        for row in args.rows:
            label, _, images = row.partition("=")
            images = [i for i in images.split(",") if i]
            images += [""] * (args.cols - len(images))
            for col, img in enumerate(images[: args.cols]):
                path = resolve(img) if img else None
                src = str(path) if path and path.exists() else str(blank)
                cap = f"{label} · {Path(img).stem}" if img else label
                cmd += ["-label", cap, src]
        cmd += ["-tile", f"{args.cols}x", "-geometry", f"{TILE}+6+6", "-pointsize", "14",
                "-background", "#1e1e1e", "-fill", "#dddddd"]
        if args.title:
            cmd += ["-title", args.title]
        cmd.append(str(resolve(args.out) if not Path(args.out).is_absolute() else args.out))
        subprocess.run(cmd, check=True)
    print(args.out, file=sys.stderr)


if __name__ == "__main__":
    main()
