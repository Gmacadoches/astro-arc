#!/usr/bin/env python3
"""Renders one sample per art style and writes docs/styles.md — the gallery the
README points at.

Everything except the style is PINNED: one reading, one visual signature, one
Stage 1.5 amplification, one dial cell, one register pair. So the seven images
differ by style and nothing else, which is the only way a style gallery is
honest — otherwise you are comparing seven different scenes and calling it a
comparison of styles.

Images are written as JPEG at 900px, not the 1600x900 PNG the pipeline
produces. Since 2026-09-13 the repository IS the plugin, so every user clones
these: seven full-size PNGs would add ~21MB to that clone to show seven
thumbnails. JPEG at this size lands near 100KB each, which is the same trade
astro-arc-export-theme already makes for its preview.

Costs about $0.10 at the High preset: Stage 2 and a render per style, with the
expensive shared stages paid once.

Usage:
    build_style_gallery.py [--register "botanical"] [--reading path]
                           [--styles a,b,c] [--no-image]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import llm_pipeline as lp  # noqa: E402
from openai_image_gen import generate as openai_generate  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT_IMAGES = REPO / "docs" / "images" / "styles"
OUT_PAGE = REPO / "docs" / "styles.md"
STATE_DIR = Path.home() / ".local/state/omarchy/astro-arc"
CONFIG = Path.home() / ".local/state/omarchy/settings/astro-arc.json"

THUMB_PX = 900


def newest_reading():
    readings = sorted(STATE_DIR.glob("reading-*.json"), key=lambda p: p.stat().st_mtime)
    if not readings:
        raise SystemExit("No reading-*.json in %s — generate once first, or pass --reading." % STATE_DIR)
    return readings[-1]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--register", default="botanical",
                    help="primary register to pin (default: botanical)")
    ap.add_argument("--secondary", default="geological", help="secondary register to pin")
    ap.add_argument("--reading", default=None)
    ap.add_argument("--styles", default=None, help="comma-separated subset")
    ap.add_argument("--no-image", action="store_true", help="prompts only — free")
    args = ap.parse_args()

    config = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    stage1_model = config.get("stage1Model") or "gpt-4o-mini"
    stage2_model = config.get("stage2Model") or "gpt-4o-mini"
    image_model = config.get("openaiModel") or "gpt-image-1-mini"
    quality = config.get("openaiQuality") or "low"

    reading = json.loads(Path(args.reading or newest_reading()).read_text())
    period_key = reading["arc"]["periodKey"]

    styles = lp.load_styles()
    wanted = [s.strip() for s in args.styles.split(",")] if args.styles else list(styles)
    wanted = [s for s in wanted if s in styles]
    if not wanted:
        raise SystemExit("No matching styles in styles.toml.")

    # --- pin everything that is not the style ------------------------------
    dial = lp.dial_from_texture(reading["arc"].get("texture"))
    stage1, _ = lp.stage1_interpret(reading, stage1_model, period_key, config)
    signature, _ = lp.get_visual_signature(reading["natal"], config, stage2_model)
    amplification, _ = lp.stage15_amplify(stage1, stage1_model, period_key, config, dial=dial)
    cliches = lp.load_cliches()

    OUT_IMAGES.mkdir(parents=True, exist_ok=True)
    rows, total = [], 0.0

    for i, key in enumerate(wanted, start=1):
        label, guidance = lp.load_style_info(key)
        print("[%d/%d] %s" % (i, len(wanted), label), file=sys.stderr)

        prompt, tags, has_people, cost = lp.stage2_image_prompt(
            stage1, signature, args.register, [], cliches, stage2_model,
            label, guidance,
            secondary_register=args.secondary,
            amplification=amplification,
            dial=dial,
            register_guidance=lp.load_register_guidance(args.register),
        )
        total += cost or 0.0
        final = prompt + ", " + lp.load_style_suffix(key, has_people)

        rel = "images/styles/%s.jpg" % key
        if not args.no_image:
            png = OUT_IMAGES / ("%s.png" % key)
            _, info = openai_generate(final, png, image_model, quality, 1600, 900)
            total += info.get("cost") or 0.0
            from PIL import Image
            img = Image.open(png).convert("RGB")
            img.thumbnail((THUMB_PX, THUMB_PX), Image.LANCZOS)
            img.save(OUT_IMAGES / ("%s.jpg" % key), quality=86, optimize=True, progressive=True)
            png.unlink()

        rows.append({"key": key, "label": label, "prompt": prompt, "rel": rel,
                     "tags": tags, "guidance": guidance})

    # --- the page ----------------------------------------------------------
    out = [
        "# Art styles",
        "",
        "One sample per preset in [`backend/pipeline/styles.toml`](../backend/pipeline/styles.toml).",
        "",
        "Every one of these is the **same** reading, the same visual signature, the",
        "same Stage 1.5 objects, the same dial cell and the same two material",
        "registers (`%s` + `%s`). Only the style differs — otherwise this would be" % (args.register, args.secondary),
        "seven different scenes rather than a comparison of styles.",
        "",
        "Change the style from the widget's Style dropdown, or add your own preset",
        "to `styles.toml` — it is read fresh on every generation.",
        "",
    ]
    for r in rows:
        out += [
            "## %s" % r["label"],
            "",
            "`%s`" % r["key"],
            "",
            "![%s](%s)" % (r["label"], r["rel"]),
            "",
            "> %s" % r["guidance"],
            "",
        ]
    OUT_PAGE.write_text("\n".join(out) + "\n", encoding="utf-8")

    print("\nWrote %s and %d image(s)  ($%.4f)"
          % (OUT_PAGE.relative_to(REPO), len(rows), total), file=sys.stderr)


if __name__ == "__main__":
    main()
