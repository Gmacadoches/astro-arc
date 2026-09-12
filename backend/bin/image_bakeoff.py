#!/usr/bin/env python3
"""Render ONE fixed prompt across image models and quality tiers, measuring what
each actually costs and saving every result for side-by-side comparison.

Why this exists separately from sweep.py: that harness varies the *prompt* (it
sweeps registers) while holding the renderer fixed. This does the opposite. The
question it answers — "is the cheaper model genuinely better, or did it just get
an easier scene?" — cannot be answered by the existing sweep, because there the
scene changes with every sample.

It also measures rather than estimates. The projected cost of an unrendered
combination borrows gpt-image-1's published per-tier token counts, and that
assumption has already been measured wrong by 17x on a newer model: a render
projected at $0.1888 actually cost $0.0113. Every number this prints is real
usage returned by the API.

Usage:
    image_bakeoff.py --out NAME --combos "model:quality,model:quality,..."
                     [--prompt-from REVIEW_DIR] [--size 1600x900]

Costs real money. It prints a projected total and requires --yes to proceed.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from model_catalog import image_cost, load_rates, render_size_for  # noqa: E402
from openai_image_gen import generate  # noqa: E402

OUT_ROOT = Path.home() / ".local/state/omarchy/astro-arc/verification"


def prompt_from_review(review_dir):
    """Pull the exact final prompt out of a generated review page, so the test
    runs the same text a real generation ran rather than a reconstruction."""
    html = (Path(review_dir) / "index.html").read_text(encoding="utf-8")
    match = re.search(
        r'<div class="prompt-label">Final image prompt</div>\s*(.*?)\s*</div>', html, re.S
    )
    if not match:
        raise SystemExit(f"No final prompt found in {review_dir}")
    return re.sub(r"<[^>]+>", "", match.group(1)).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="folder name under verification/")
    ap.add_argument("--combos", required=True, help="model:quality pairs, comma separated")
    ap.add_argument("--prompt-from", help="review dir to take the final prompt from")
    ap.add_argument("--prompt", help="literal prompt text (overrides --prompt-from)")
    ap.add_argument("--size", default="1600x900")
    ap.add_argument("--yes", action="store_true", help="actually spend money")
    args = ap.parse_args()

    if args.prompt:
        prompt = args.prompt
    elif args.prompt_from:
        prompt = prompt_from_review(args.prompt_from)
    else:
        raise SystemExit("Need --prompt or --prompt-from")

    width, height = (int(x) for x in args.size.split("x"))
    render_size = render_size_for(width, height)
    combos = [tuple(c.split(":")) for c in args.combos.split(",")]
    rates = load_rates()

    print(f"Prompt ({len(prompt.split())} words), rendered at {render_size}:")
    print(f"  {prompt[:150]}…\n")
    print(f"{'model':26} {'tier':8} {'projected':>10}")
    projected_total = 0.0
    for model, quality in combos:
        cost, source = image_cost(model, quality, render_size, rates)
        projected_total += cost or 0
        shown = f"${cost:.4f}" if cost is not None else "unknown"
        print(f"  {model:24} {quality:8} {shown:>10} ({source})")
    print(f"\n  projected total: ${projected_total:.4f}"
          f"  — projections are unreliable for newer models; real cost may differ a lot")

    if not args.yes:
        print("\nDry run. Re-run with --yes to spend.")
        return

    out_dir = OUT_ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "00_prompt.txt").write_text(prompt)

    results, actual_total = [], 0.0
    print()
    for i, (model, quality) in enumerate(combos, 1):
        stem = f"{i:02d}_{model.replace('.', '-')}_{quality}"
        try:
            _, info = generate(prompt, out_dir / f"{stem}.png", model, quality, width, height)
            cost = info.get("cost")
            actual_total += cost or 0
            usage = info.get("usage") or {}
            row = {
                "model": model, "quality": quality, "cost": cost,
                "outputTokens": usage.get("output_tokens"),
                "inputTokens": usage.get("input_tokens"),
                "file": f"{stem}.png",
            }
            print(f"  {model:24} {quality:8} ${cost:.4f}  out={usage.get('output_tokens')}")
        except Exception as exc:  # noqa: BLE001 — one failure must not lose the rest
            row = {"model": model, "quality": quality, "error": str(exc)[:200]}
            print(f"  {model:24} {quality:8} FAILED: {str(exc)[:110]}")
        results.append(row)

    (out_dir / "00_results.json").write_text(json.dumps(
        {"prompt": prompt, "renderSize": render_size, "results": results,
         "actualTotal": round(actual_total, 6)}, indent=2))
    print(f"\n  actual total: ${actual_total:.4f}")
    print(f"  {out_dir}")


if __name__ == "__main__":
    main()
