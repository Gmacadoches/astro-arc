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
same recency pressure a real run would:

  - the reading and its stored dial, per day, from cache
  - the amplification, fresh, with the archetype and actor-kind avoid lists
    built up day by day
  - the register pair, from the real rotation against an accumulating history
  - the avoided concept tags, from that same history

Only the visual signature is shared, which is correct: it is one per natal
chart by design, and the series is supposed to read as one hand.

Nothing production owns is written. cache=False on every stage, output goes to
verification/<name>/, and history is synthetic and in-memory. The one exception
is the signature, which is cached on a hash of the birth data and will hit that
cache rather than regenerate.

Costs real money (about $0.025 a day with renders). Prints a projection and
needs --yes.

    python3 backend/bin/replay_days.py --days 8
    python3 backend/bin/replay_days.py --days 8 --yes
"""

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm_pipeline as lp  # noqa: E402
from openai_image_gen import generate as openai_generate  # noqa: E402
from paths import STATE_DIR  # noqa: E402
from preflight_actors import archetype_family  # noqa: E402

READINGS_DIR = STATE_DIR / "pipeline" / "readings"
VERIFY_ROOT = STATE_DIR / "verification"
CONFIG_PATH = Path.home() / ".local/state/omarchy/settings/astro-arc.json"


def load_days(limit):
    """(key, stage1, dial) newest-last, each with the dial that actually
    applied that day rather than one recomputed from today's Moon."""
    out = []
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
        dial = dict(lp.NO_TEXTURE_DIAL)
        meta = STATE_DIR / f"pipeline-meta-{key}.json"
        if meta.exists():
            try:
                stored = (json.loads(meta.read_text()).get("dial") or {})
                dial.update({k: v for k, v in stored.items() if v is not None})
            except (json.JSONDecodeError, OSError):
                pass
        out.append((key, reading, dial))
    return out[-limit:]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=8)
    ap.add_argument("--out", default="day-variation")
    ap.add_argument("--style", default=None)
    ap.add_argument("--quality", default="medium")
    ap.add_argument("--no-image", action="store_true")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    config = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    style_key = args.style or config.get("artStyle") or "symbolist"
    stage1_model = config.get("stage1Model") or "gpt-4o-mini"
    stage2_model = config.get("stage2Model") or "gpt-4o-mini"
    image_model = config.get("openaiModel") or "gpt-image-1-mini"
    width, height = (int(x) for x in (config.get("backgroundSize") or "1600x900").split("x"))

    days = load_days(args.days)
    if not days:
        print(f"No cached daily readings under {READINGS_DIR}.")
        return 1

    per = 0.012 if args.no_image else 0.025
    print(f"{len(days)} days on {style_key}, everything varying "
          f"({'prompts only' if args.no_image else 'with renders'})")
    print(f"projected cost: about ${len(days) * per:.2f}")
    if not args.yes:
        print("\nDry run. Re-run with --yes to spend it.")
        return 0

    out_dir = VERIFY_ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    registers = lp.load_registers()
    style_label, style_world = lp.load_style_info(style_key)
    cliches = lp.load_cliches()
    # The signature needs a natal block, which the per-day reading cache does
    # not hold — that file is Stage 1's output, not the chart. Take it from the
    # newest full reading-*.json, which is the chart itself.
    full = sorted(STATE_DIR.glob("reading-*.json"))
    signature, _ = lp.get_visual_signature(
        json.loads(full[-1].read_text())["natal"], config, stage2_model)

    history, rows, total = [], [], 0.0
    used_constellations, used_kinds = [], []

    for i, (key, stage1, dial) in enumerate(days, start=1):
        avoid_arch = list(reversed(used_constellations))[:lp.AMPLIFY_AVOID_WINDOW]
        avoid_kind = list(reversed(used_kinds))[:lp.ACTOR_VETO_WINDOW]
        amp, c1 = lp.stage15_amplify(
            stage1, stage1_model, key, config, cache=False, dial=dial,
            avoid_archetypes=avoid_arch, avoid_kinds=avoid_kind,
            style_key=style_key, style_world=lp.load_style_world(style_key),
        )
        total += c1 or 0.0
        used_constellations.append(amp.get("constellation", ""))
        used_kinds.append(amp.get("actorKind", ""))

        primary, secondary = lp.pick_registers(
            history, registers, dial.get("registerCohesion", "collide"))
        avoid_tags = lp.avoid_concepts(history)

        kwargs = {"amplification": amp, "dial": dial,
                  "register_guidance": lp.load_register_guidance(primary),
                  "style_brief": lp.load_style_brief(style_key)}
        if secondary:
            kwargs["secondary_register"] = secondary
        prompt, tags, has_people, c2 = lp.stage2_image_prompt(
            stage1, signature, primary, avoid_tags, cliches, stage2_model,
            style_label, style_world, **kwargs)
        total += c2 or 0.0

        suffix = lp.load_style_suffix(style_key, has_people)
        final = f"{prompt}, {suffix}" if suffix else prompt
        stem = f"{i:02d}_{key}"

        row = {
            "day": key, "register": primary, "secondary": secondary,
            "actor": amp.get("actor", ""), "actorKind": amp.get("actorKind", ""),
            "family": archetype_family(amp.get("constellation", "")),
            "anomalyType": amp.get("anomalyType", ""),
            "anomaly": amp.get("anomaly", ""),
            "polarity": (dial.get("tonalDirection") or "")[:14],
            "hasPeople": has_people, "promptWords": len(prompt.split()),
            "conceptTags": "|".join(tags), "imagePrompt": prompt,
            # The whole amplification and the final prompt, so a batch can be
            # filed into the archive afterwards without re-rendering. The first
            # run of this harness saved neither, and the renders were good, so
            # those eight entries carry reconstructed metadata with the
            # constellation and object list blank.
            "finalPrompt": final,
            "constellation": amp.get("constellation", ""),
            "movement": amp.get("movement", ""),
            "affect": amp.get("affect", ""),
            "amplificationObjects": amp.get("objects") or [],
            "visualSignature": signature,
            "stage1Model": stage1_model, "stage2Model": stage2_model,
            "artStyle": style_key,
        }

        if not args.no_image:
            try:
                _, info = openai_generate(final, out_dir / f"{stem}.png",
                                          image_model, args.quality, width, height)
                total += info.get("cost") or 0.0
            except Exception as exc:  # noqa: BLE001 — one bad render must not end the run
                row["imageError"] = str(exc)[:200]
                print(f"    render failed: {str(exc)[:120]}", file=sys.stderr)

        (out_dir / f"{stem}.json").write_text(json.dumps(row, indent=2))
        rows.append(row)
        history.insert(0, {"register": primary, "secondaryRegister": secondary,
                           "conceptTags": tags,
                           "timestamp": datetime.now(timezone.utc).isoformat()})
        print(f"  {key}  {primary[:22]:22} {row['actorKind']:20} {row['family'][:26]}")

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
