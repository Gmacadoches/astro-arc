#!/usr/bin/env python3
"""Renders one sample per art style and writes docs/styles.md — the gallery the
README points at.

Everything except the style is PINNED: one reading, one
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
import re
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
    image_model = config.get("openaiModel") or "gpt-image-1-mini"
    quality = config.get("openaiQuality") or "low"

    reading = json.loads(Path(args.reading or newest_reading()).read_text())
    period_key = reading["arc"]["periodKey"]

    styles = lp.load_styles()
    # The gallery documents the shipped set; a user's own themes are named explicitly.
    shipped = [k for k, t in styles.items() if t.source == "shipped"]
    wanted = [s.strip() for s in args.styles.split(",")] if args.styles else shipped
    wanted = [s for s in wanted if s in styles]
    if not wanted:
        raise SystemExit("No matching styles.")

    # --- pin everything that is not the style ------------------------------
    dial = lp.dial_from_texture(reading["arc"].get("texture"))
    # Uncached, so building the gallery never seeds the production readings cache.
    stage1, _ = lp.stage1_interpret(reading, stage1_model, period_key, config, cache=False)

    # The amplification used to be pinned here, beside Stage 1, on the premise that everything except the style should be
    # held constant. That premise died on 2026-09-20: the style is now a
    # Stage 1.5 input, because the objects and the anomaly are named in the
    # world's own terms. Pinning one amplification across styles would show
    # every world holding the same foreign objects, which is precisely the
    # failure this gallery would be used to check for.
    #
    # So it moves into the loop, one per style. What the gallery compares is
    # now "same reading, different world" rather than "same
    # objects, different rendering" — which is the honest comparison, and the
    # only one that can show a world doing its job. Costs one extra Stage 1.5
    # call per style, about $0.003.

    OUT_IMAGES.mkdir(parents=True, exist_ok=True)
    rows, total = [], 0.0

    for i, key in enumerate(wanted, start=1):
        label = lp.load_styles()[key]["label"]
        print("[%d/%d] %s" % (i, len(wanted), label), file=sys.stderr)
        # One composition per world, with its own draws (verbs, space,
        # condition, highlight mode) as production applies them, the
        # registers pinned, no history and no caption check.
        result = lp.compose(stage1, reading["arc"], dial, key, config, [], caption="skip",
                            registers=(args.register, args.secondary))
        total += (result["stage15Cost"] or 0.0) + (result["stage2Cost"] or 0.0)
        prompt, tags, final = result["imagePrompt"], result["conceptTags"], result["finalPrompt"]

        rel = "images/styles/%s.jpg" % key
        if not args.no_image:
            png = OUT_IMAGES / ("%s.png" % key)
            _, info = openai_generate(final, png, image_model, quality, 1600, 900)
            total += info.get("cost") or 0.0
            from image_fit import thumbnail_jpeg
            thumbnail_jpeg(png, OUT_IMAGES / ("%s.jpg" % key), THUMB_PX, quality=86)
            png.unlink()

        rows.append({"key": key, "label": label, "prompt": prompt, "rel": rel, "tags": tags})

    # --- the page ----------------------------------------------------------
    # Rewritten 2026-10-10: it quoted each style's `guidance`, which the
    # charters replaced. It now shows each world's era, myth and signature.
    out = [
        "# The seven worlds",
        "",
        "Each style in [`backend/pipeline/styles/`](../backend/pipeline/styles/) is a technique and a "
        "**world**: a charter that says what the world is made of, who lives there, which myths its stories "
        "come from, what its highlight is, and the one thing that marks a picture as unmistakably that world. "
        "[`SKILL.md`](../skills/astro-arc-style/SKILL.md) describes every field.",
        "",
        "Every image below is the **same day's reading** and the **same material registers** (`%s` + `%s`), "
        "rendered at the same settings. Each world built its own scene from them: that is the point. Change "
        "the style from the widget's Style dropdown; the file is read fresh on every generation."
        % (args.register, args.secondary),
        "",
    ]
    styles = lp.load_styles()
    for r in rows:
        world = (styles.get(r["key"]) or {}).get("world") or {}
        brief = (styles.get(r["key"]) or {}).get("brief") or {}
        sig = (brief.get("signature") or brief.get("impossible") or "").split("\n")[0]
        sig = re.sub(r"^[A-Z ]+ — ", "", sig).strip()
        sig = re.sub(r"^applies to every image: ", "", sig)
        sig = " ".join(re.split(r"(?<=\.)\s+", sig)[:2])
        sig = sig[:1].upper() + sig[1:]
        out += ["## %s" % r["label"], "", "`%s`" % r["key"], "", "![%s](%s)" % (r["label"], r["rel"]), ""]
        if world.get("era"):
            out += ["**The world.** %s" % world["era"], ""]
        if world.get("myth"):
            out += ["**Its stories come from:** %s" % world["myth"], ""]
        if sig:
            out += ["**What marks it.** %s" % sig, ""]
        out += ["<details><summary>The prompt for this image</summary>", "", "> %s" % r["prompt"], "",
                "</details>", ""]
    OUT_PAGE.write_text("\n".join(out) + "\n", encoding="utf-8")

    print("\nWrote %s and %d image(s)  ($%.4f)"
          % (OUT_PAGE.relative_to(REPO), len(rows), total), file=sys.stderr)


if __name__ == "__main__":
    main()
