#!/usr/bin/env python3
"""Render a sweep's Stage 2 prompts again under the style suffix as it is
NOW in its theme file — so two renders of the same prompt differ only in the
suffix. Written for STYLE-OVERHAUL Prompt 3 (suffix rewrites); a fresh sweep
would redraw the material and the prompt and confound the comparison.

Usage:
    suffix_render.py --from <sweep> --out <name> [--indices 1,2,3] [--style S]
    suffix_render.py --prompt "text" --style S --out <name> --stem NAME

--style overrides the sweep's own style (to render one prompt in another
style's technique). Output: verification/<name>/<stem>.png plus <stem>.json
with the exact final prompt and cost. Renders at the configured model and
tier, like production.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm_pipeline as lp  # noqa: E402
from openai_image_gen import generate  # noqa: E402
from paths import CONFIG_FILE, VERIFY_DIR  # noqa: E402
from providers import provider_of  # noqa: E402



def render(prompt, style, has_people, out_dir, stem, config):
    final = lp.final_image_prompt(prompt, lp.Catalog.load().style(style), has_people)
    model = config.get("openaiModel") or "gpt-image-1-mini"
    quality = config.get("openaiQuality") or "medium"
    width, height = (int(x) for x in (config.get("backgroundSize") or "1600x900").split("x"))
    record = {"style": style, "imagePrompt": prompt, "finalPrompt": final, "hasPeople": has_people}
    try:
        _, cost = generate(final, out_dir / f"{stem}.png", model, quality, width, height, provider_of(config))
        record["imageCost"] = cost.get("cost")
    except Exception as exc:  # noqa: BLE001 — one refusal must not stop the batch
        record["imageError"] = str(exc)[:300]
        print(f"{stem}: {exc}", file=sys.stderr)
    (out_dir / f"{stem}.json").write_text(json.dumps(record, indent=2))
    return record


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="source")
    ap.add_argument("--indices")
    ap.add_argument("--prompt")
    ap.add_argument("--stem", default="prompt")
    ap.add_argument("--style")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    config = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    out_dir = VERIFY_DIR / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.prompt:
        if not args.style:
            ap.error("--prompt needs --style")
        render(args.prompt, args.style, False, out_dir, args.stem, config)
        return
    if not args.source:
        ap.error("give --from or --prompt")
    source = Path(args.source)
    if not source.is_dir():
        source = VERIFY_DIR / args.source
    data = json.loads((source / "00_sweep.json").read_text())
    wanted = {int(i) for i in args.indices.split(",")} if args.indices else None
    total = 0.0
    for s in data["samples"]:
        if wanted and s["index"] not in wanted:
            continue
        stem = f"{s['index']:02d}_{s['primaryRegister'].replace(' ', '_')}"
        print(f"rendering {stem}", file=sys.stderr)
        rec = render(s["imagePrompt"], args.style or data["style"], bool(s.get("hasPeople")),
                     out_dir, stem, config)
        total += rec.get("imageCost") or 0.0
    print(f"{out_dir}  (${total:.3f})", file=sys.stderr)


if __name__ == "__main__":
    main()
