#!/usr/bin/env python3
"""Replay cached Stage 1 readings through Stage 1.5 and measure archetype range.

Built for B0 of the actor redesign, after preflight_actors.py found Stage 1.5
collapsing onto one archetype: 10 of 12 days reached for "the separation of the
World Parents" and only 2 distinct archetypes were ever used, while the Stage 1
readings underneath varied across the full emotional range. The question this
answers is narrow and measurable: does a change to STAGE15_SYSTEM actually widen
the range, or does it only feel like it should?

Same discipline as sweep.py, and for the same reason its header gives — a
comparison that is not cheap and self-archiving does not get re-run. Everything
that is not under test is pinned:

  - the readings are the REAL cached Stage 1 outputs, not fresh ones, so the
    input is byte-identical to the run that produced the baseline
  - each day's dial is the one stored in that day's pipeline-meta, so tonal
    direction and object range are the ones that actually applied
  - cache=False throughout, so the production amplification cache is never
    read for a hit and never written

Arms:
  rule     the revised prompt alone, no recency list
  full     the revised prompt plus RECENTLY USED, built up sequentially in
           date order, which is what would really happen day by day
  control  no recency list and the recency rule disabled, to reproduce the
           old behaviour for a same-session comparison

Costs real money (about $0.003 a call). Prints a projected total and needs
--yes to proceed.

    python3 backend/bin/replay_amplify.py --arms rule,full
    python3 backend/bin/replay_amplify.py --arms rule,full --yes
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm_pipeline as lp  # noqa: E402
from paths import STATE_DIR  # noqa: E402
from preflight_actors import archetype_family  # noqa: E402  — one definition, shared

READINGS_DIR = STATE_DIR / "pipeline" / "readings"
OUT_ROOT = STATE_DIR / "verification"
CONFIG_PATH = Path.home() / ".local/state/omarchy/settings/astro-arc.json"


def load_config():
    """Same one-liner sweep.py uses; only `stage1Model` and the natal hash
    matter here."""
    return json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}


def load_days():
    """(key, stage1_result, dial) per cached daily reading, oldest first.

    The dial comes from that day's pipeline-meta rather than being recomputed:
    recomputing it from today's Moon would pair a day's reading with a tonal
    direction it never had, which is the same confound sweep.py pins against.
    """
    days = []
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
        meta_path = STATE_DIR / f"pipeline-meta-{key}.json"
        if meta_path.exists():
            try:
                stored = (json.loads(meta_path.read_text()).get("dial") or {})
                dial.update({k: v for k, v in stored.items() if v is not None})
            except (json.JSONDecodeError, OSError):
                pass
        days.append((key, reading, dial))
    return days


def run_arm(arm, days, model, config, out_dir):
    """One arm, in date order. Returns (families, rows, cost)."""
    used, rows, total = [], [], 0.0
    for key, reading, dial in days:
        if arm == "full":
            avoid = list(reversed(used))[:lp.AMPLIFY_AVOID_WINDOW]
        else:
            avoid = []
        result, cost = lp.stage15_amplify(
            reading, model, key, config, cache=False, dial=dial, avoid_archetypes=avoid,
        )
        total += cost or 0.0
        constellation = result.get("constellation", "")
        used.append(constellation)
        fam = archetype_family(constellation)
        rows.append({
            "day": key, "arm": arm, "family": fam,
            "constellation": constellation,
            "movement": result.get("movement", ""),
            "anomaly": result.get("anomaly", ""),
            "avoidCount": len(avoid),
        })
        print(f"  {key}  {fam[:38]:38}  {constellation[:60]}")
    (out_dir / f"{arm}.json").write_text(json.dumps(rows, indent=2))
    return [r["family"] for r in rows], rows, total


def report(arm, families):
    n = len(families)
    counts = Counter(families)
    consec = sum(1 for i in range(1, n) if families[i] == families[i - 1])
    top, top_n = counts.most_common(1)[0]
    print(f"\n  {arm}: {len(counts)} distinct families across {n} days")
    print(f"  most common: {top_n}/{n} ({top_n/n:.0%})  \"{top[:44]}\"")
    print(f"  consecutive repeats: {consec}/{max(n-1,1)} ({consec/max(n-1,1):.0%})")
    return {
        "arm": arm, "days": n, "distinct": len(counts),
        "topShare": top_n / n, "consecutive": consec / max(n - 1, 1),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arms", default="rule,full", help="comma-separated: control,rule,full")
    ap.add_argument("--out", default="b0-archetype-range")
    ap.add_argument("--model", default=None, help="defaults to config stage1Model")
    ap.add_argument("--yes", action="store_true", help="actually spend money")
    args = ap.parse_args()

    config = load_config()
    model = args.model or config.get("stage1Model") or "gpt-4o-mini"
    days = load_days()
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]

    if not days:
        print(f"No cached daily readings under {READINGS_DIR}.")
        return 1

    calls = len(days) * len(arms)
    print(f"{len(days)} cached days x {len(arms)} arms = {calls} Stage 1.5 calls on {model}")
    print(f"projected cost: about ${calls * 0.003:.2f}")
    if not args.yes:
        print("\nDry run. Re-run with --yes to spend it.")
        return 0

    out_dir = OUT_ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    summaries, total = [], 0.0
    for arm in arms:
        print(f"\n--- arm: {arm} ---")
        # The control arm reproduces the pre-B0 behaviour by stripping the new
        # rule back out of the prompt, so both arms can be compared inside one
        # session rather than against a months-old cache.
        original = lp.STAGE15_SYSTEM
        if arm == "control":
            lp.STAGE15_SYSTEM = re.sub(
                r"- \*\*Reach past the obvious archetype\.\*\*.*?\n", "",
                original, flags=re.DOTALL | re.MULTILINE, count=1)
        try:
            families, _, cost = run_arm(arm, days, model, config, out_dir)
        finally:
            lp.STAGE15_SYSTEM = original
        total += cost
        summaries.append(report(arm, families))

    print("\n" + "=" * 70)
    print("SUMMARY".center(70))
    print("=" * 70)
    print(f"  {'arm':10} {'distinct':>9} {'top share':>10} {'consec rpt':>11}")
    print(f"  {'baseline':10} {2:>9} {'83%':>10} {'82%':>11}   (from the cache)")
    for s in summaries:
        print(f"  {s['arm']:10} {s['distinct']:>9} {s['topShare']:>9.0%} {s['consecutive']:>10.0%}")
    print(f"\n  spent: ${total:.3f}   written to: {out_dir}")
    print("\n  B0 target: 6+ distinct families, no family above ~35%.")
    (out_dir / "summary.json").write_text(json.dumps(summaries, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
