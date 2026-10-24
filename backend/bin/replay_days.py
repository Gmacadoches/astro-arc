#!/usr/bin/env python3
"""Replay real days end to end, with everything varying, the way production does.

This answers a question neither existing harness can. sweep.py pins everything
except the register, on purpose, so that register differences are not
confounded with reading differences — which means a sweep's samples SHOULD look
alike, and their looking alike says nothing about whether consecutive days
will. replay_amplify.py varies the day but stops at Stage 1.5 and never renders.

So: the same question the user actually has. Will tomorrow's wallpaper look
different from today's?

Everything that moves in production moves here, in date order, accumulating the
same recency pressure a real run would. Each day is one llm_pipeline.compose(),
the composition production runs:

  - the reading and its stored dial, per day, from cache
  - the amplification, fresh, with the archetype and actor-kind avoid lists
    built up day by day, and the material avoiding recent days' objects
  - the register pair, from the real rotation against an accumulating history
  - the style's draws, avoiding what recent days drew (unless a day's stored
    draws are reused, see replay)
  - the avoided concept tags, from that same history
  - the melodrama check, resampling Stage 2 on a failure

Nothing production owns is written. cache=False on every stage, output goes to
verification/<name>/, and history is synthetic and in-memory.

Costs real money. Prints a projection from the model rate table
(model_catalog) for the configured models and needs --yes.

    python3 backend/bin/replay_days.py --days 8
    python3 backend/bin/replay_days.py --days 8 --yes
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm_pipeline as lp  # noqa: E402
import model_catalog as mc  # noqa: E402
from sweep import dial_for_day, report_filled  # noqa: E402
from openai_image_gen import generate as openai_generate  # noqa: E402
from paths import STATE_DIR  # noqa: E402
from preflight_actors import archetype_family  # noqa: E402

READINGS_DIR = STATE_DIR / "pipeline" / "readings"
VERIFY_ROOT = STATE_DIR / "verification"
CONFIG_PATH = Path.home() / ".local/state/omarchy/settings/astro-arc.json"


def load_days(limit, cell, textured_only=False):
    """(key, stage1, dial) newest-last, each with the dial that actually
    applied that day rather than one recomputed from today's Moon. The base
    dial comes from the day's own stored texture; `cell` (sweep.py's format)
    stands in for a day that has none (see sweep.dial_for_day), or with
    `textured_only` the day is skipped, so every dial is one a day really had."""
    out, filled_days = [], []
    for path in sorted(READINGS_DIR.glob("*.json")):
        key = path.stem
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", key):
            continue
        try:
            reading = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not all(reading.get(k) for k in ("reading", "distillation", "narrativePosition")):
            continue
        dial, source = dial_for_day(STATE_DIR, key, cell)
        if textured_only and source != "texture":
            continue
        filled_days.append((key, source))
        out.append((key, reading, dial))
    report_filled(filled_days[-limit:], cell)
    return out[-limit:]


# The dial keys draw_style_choices fills from a charter. A stored day carries
# the draws of the style it ran on; replaying it on another theme (--redraw,
# and astro-arc-style meaning) drops them so that theme draws its own.
STYLE_DRAW_KEYS = ("verbs", "actorVerbs", "activity", "space", "light", "condition", "highlightMode")


def stored_draws(dial):
    """The draws a day's stored dial carries, as compose()'s `draws` pin, or
    None to draw fresh from the style. Only dials stored since per-style
    draws (2026-10-10) carry a full set; an older day's verbs came from the
    global tables, which no longer exist, so it draws like a new run."""
    if not all(k in dial for k in ("verbs", "actorVerbs", "activity", "condition")):
        return None
    draws = {"processVerbs": dial["verbs"].split(", "),
             "activity": dial["activity"],
             "actorVerbs": dial["actorVerbs"].split(", "),
             "condition": dial["condition"]}
    for key in ("space", "highlightMode"):
        if key in dial:
            draws[key] = dial[key]
    return draws


def replay(style_key, days, config, out_dir=None, render=False, quality="medium",
           redraw=False, log=print):
    """Replay `days` (load_days) on `style_key` in date order, with the
    recency pressure accumulating as it would in production. Returns
    (rows, spent). Writes one JSON per day (and the render) under `out_dir`
    when it is given; nothing production owns is ever written."""
    image_model = config.get("openaiModel") or "gpt-image-1-mini"
    width, height = (int(x) for x in (config.get("backgroundSize") or "1600x900").split("x"))
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)

    history, rows, total = [], [], 0.0
    used_constellations, used_kinds = [], []

    for i, (key, stage1, dial) in enumerate(days, start=1):
        if redraw:
            dial = {k: v for k, v in dial.items() if k not in STYLE_DRAW_KEYS}
        result = lp.compose(
            stage1, {"periodKey": key, "frequency": "daily"}, dial, style_key, config, history,
            caption="resample", draws=stored_draws(dial),
            avoid_archetypes=list(reversed(used_constellations))[:lp.AMPLIFY_AVOID_WINDOW],
            avoid_kinds=list(reversed(used_kinds))[:lp.ACTOR_VETO_WINDOW])
        meaning = result["meaning"]
        total += (result["stage15Cost"] or 0.0) + (result["stage2Cost"] or 0.0)
        used_constellations.append(meaning.get("constellation", ""))
        used_kinds.append(meaning.get("actorKind", ""))
        dial = result["dial"]
        prompt = result["imagePrompt"]
        final = result["finalPrompt"]
        stem = f"{i:02d}_{key}"

        row = {
            "day": key, "register": result["register"], "secondary": result["secondaryRegister"],
            "actor": meaning.get("actor", ""), "actorKind": meaning.get("actorKind", ""),
            "family": archetype_family(result["constellation"] or ""),
            "anomalyType": result["anomalyType"],
            "anomaly": result["anomaly"],
            "polarity": (dial.get("tonalDirection") or "")[:14],
            # The day's real hard/soft, from its stored texture.
            "dialPolarity": dial.get("polarity", ""),
            "hasPeople": result["hasPeople"], "promptWords": len(prompt.split()),
            "conceptTags": "|".join(result["conceptTags"]), "imagePrompt": prompt,
            # The whole amplification and the final prompt, so a batch can be
            # filed into the archive afterwards without re-rendering. The first
            # run of this harness saved neither, and the renders were good, so
            # those eight entries carry reconstructed metadata with the
            # constellation and object list blank.
            "finalPrompt": final,
            "constellation": result["constellation"],
            "movement": result["movement"],
            "affect": result["affect"],
            "amplificationObjects": result["amplificationObjects"] or [],
            "stage1Model": result["stage1Model"], "stage2Model": result["stage2Model"],
            "artStyle": style_key,
            "captionCheck": result["captionCheck"],
        }

        if render:
            try:
                _, info = openai_generate(final, out_dir / f"{stem}.png",
                                          image_model, quality, width, height)
                total += info.get("cost") or 0.0
            except Exception as exc:  # noqa: BLE001 — one bad render must not end the run
                row["imageError"] = str(exc)[:200]
                print(f"    render failed: {str(exc)[:120]}", file=sys.stderr)

        if out_dir is not None:
            (out_dir / f"{stem}.json").write_text(json.dumps(row, indent=2))
        rows.append(row)
        history.insert(0, result["historyEntry"])
        log(f"  {key}  {result['register'][:22]:22} {row['actorKind']:20} {row['family'][:26]}")
    return rows, total


def projected_cost(config, render, quality):
    """(per_day, per_day_if_every_check_resamples, unknown) in dollars, from
    the rate table for the models `replay` will use: two Stage 1.5 calls
    (meaning, material), Stage 2 and the melodrama check, plus a render.
    `unknown` names each model with no rate; the totals are None then."""
    model1 = config.get("stage1Model") or "gpt-4o-mini"
    model2 = config.get("stage2Model") or "gpt-4o-mini"
    caption_model = config.get("captionModel") or lp.CAPTION_MODEL
    stage15, _ = mc.stage_cost("stage15", model1)
    stage2, _ = mc.stage_cost("stage2", model2)
    caption, _ = mc.stage_cost("caption", caption_model)
    image = 0.0
    unknown = [m for m, c in ((model1, stage15), (model2, stage2), (caption_model, caption))
               if c is None]
    if render:
        image_model = config.get("openaiModel") or "gpt-image-1-mini"
        width, height = (int(x) for x in (config.get("backgroundSize") or "1600x900").split("x"))
        image, _ = mc.image_cost(image_model, quality, mc.render_size_for(width, height))
        if image is None:
            unknown.append(image_model)
    if unknown:
        return None, None, unknown
    once = 2 * stage15 + stage2 + caption + image
    worst = once + lp.CAPTION_RESAMPLES * (stage2 + caption)
    return once, worst, []


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=8)
    ap.add_argument("--out", default="day-variation")
    ap.add_argument("--cell", default="soft/mid/1/mid",
                    help="dial cell for a day with no usable stored texture: polarity/intensityBand/multiplicity/exposureBand, as sweep.py --cell")
    ap.add_argument("--style", default=None)
    ap.add_argument("--quality", default="medium")
    ap.add_argument("--no-image", action="store_true")
    ap.add_argument("--redraw", action="store_true",
                    help="draw verbs, light, space and the rest from --style instead of reusing each day's stored draws")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    config = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    style_key = args.style or config.get("artStyle") or "symbolist"
    lp.require_charter(style_key)

    days = load_days(args.days, args.cell)
    if not days:
        print(f"No cached daily readings under {READINGS_DIR}.")
        return 1

    print(f"{len(days)} days on {style_key}, everything varying "
          f"({'prompts only' if args.no_image else 'with renders'})")
    once, worst, unknown = projected_cost(config, not args.no_image, args.quality)
    if unknown:
        print(f"projected cost: unknown (no rate for {', '.join(sorted(set(unknown)))})")
    else:
        print(f"projected cost: about ${len(days) * once:.3f} "
              f"(up to ${len(days) * worst:.3f} if every melodrama check resamples)")
    if not args.yes:
        print("\nDry run. Re-run with --yes to spend it.")
        return 0

    out_dir = VERIFY_ROOT / args.out
    rows, total = replay(style_key, days, config, out_dir=out_dir,
                         render=not args.no_image, quality=args.quality, redraw=args.redraw)

    cols = ["day", "register", "secondary", "actorKind", "actor", "family",
            "anomalyType", "hasPeople", "promptWords", "conceptTags"]
    with (out_dir / "00_days.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    def spread(field):
        vals = [r[field] for r in rows]
        uniq = len(set(vals))
        consec = sum(1 for a, b in zip(vals, vals[1:]) if a == b)
        return uniq, len(vals), consec / max(len(vals) - 1, 1)

    print("\n" + "=" * 64)
    print(f"  {'axis':14} {'distinct':>9} {'of':>4} {'consecutive repeat':>20}")
    print("-" * 64)
    for field, label in (("register", "register"), ("actorKind", "actor kind"),
                         ("family", "archetype"), ("anomalyType", "anomaly type")):
        u, n, c = spread(field)
        print(f"  {label:14} {u:>9} {n:>4} {c:>19.0%}")
    tags = [set(r["conceptTags"].split("|")) for r in rows]
    overlap = [len(a & b) for a, b in zip(tags, tags[1:])]
    print(f"\n  concept tags shared between consecutive days: "
          f"{sum(overlap)}/{sum(len(t) for t in tags[1:])}")
    print(f"  spent: ${total:.3f}   written to: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
