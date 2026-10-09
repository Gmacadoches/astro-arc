#!/usr/bin/env python3
"""File a verification batch into the real archive, as generations.

Verification output normally stays out of the archive on purpose — a sweep is
not a generation, it has no theme snapshot, and the archive is the record of
what actually went on the desktop. This files it anyway, on request, because a
batch worth keeping is worth being able to scroll back through.

Two things it does to keep the archive honest about what these are:

  - every label is prefixed, so a test render is never mistaken for a day that
    really happened;
  - the chart is recomputed for the DATE THE BATCH REPLAYED, via astro_engine's
    ASTRO_ARC_AT override, rather than stamping today's sky on all of them.

Entries land in generations.json like any other and can be removed with
astro-arc-prune-history, so this is reversible.

    python3 backend/bin/archive_sweep.py --batch day-variation --prefix "REPLAY"
    python3 backend/bin/archive_sweep.py --batch day-variation --yes
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from paths import STATE_DIR  # noqa: E402

VERIFY_ROOT = STATE_DIR / "verification"
READINGS_DIR = STATE_DIR / "pipeline" / "readings"
CONFIG_PATH = Path.home() / ".local/state/omarchy/settings/astro-arc.json"


def chart_for(day, scratch):
    """That day's chart, recomputed. `day` is YYYY-MM-DD or None for now."""
    out = scratch / f"chart-{day or 'now'}.json"
    if out.exists():
        return out
    env = dict(os.environ)
    if day:
        env["ASTRO_ARC_AT"] = day
    proc = subprocess.run([sys.executable, str(HERE / "astro_engine.py")],
                          capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise SystemExit(f"astro_engine failed for {day}: {proc.stdout[:200]}{proc.stderr[:200]}")
    out.write_text(proc.stdout)
    return out


def meta_for(row, day, config, scratch, style_override=None):
    """A pipeline-meta-shaped file from a replay row.

    Rows written before replay_days persisted the amplification have no
    constellation, movement, affect or object list. Those are left empty
    rather than invented; the review page renders a dash.
    """
    stage1 = {}
    cached = READINGS_DIR / f"{day}.json"
    if cached.exists():
        try:
            stage1 = json.loads(cached.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    meta = {
        "reading": stage1.get("reading", ""),
        "distillation": stage1.get("distillation", ""),
        "narrativePosition": stage1.get("narrativePosition", ""),
        "artStyle": style_override or row.get("artStyle") or config.get("artStyle") or "",
        "register": row.get("register") or "",
        "secondaryRegister": row.get("secondary") or "",
        "conceptTags": [t for t in (row.get("conceptTags") or "").split("|") if t],
        "avoidedConcepts": [],
        "constellation": row.get("constellation") or "",
        "movement": row.get("movement") or "",
        "affect": row.get("affect") or "",
        "amplificationObjects": row.get("amplificationObjects") or [],
        "anomaly": row.get("anomaly") or "",
        "anomalyType": row.get("anomalyType") or "",
        "intrusion": row.get("anomaly") or "",
        "actor": row.get("actor") or "",
        "actorKind": row.get("actorKind") or "",
        "hasPeople": row.get("hasPeople"),
        "imagePrompt": row.get("imagePrompt") or "",
        "finalPrompt": row.get("finalPrompt") or row.get("imagePrompt") or "",
        "stage1Model": row.get("stage1Model") or config.get("stage1Model") or "",
        "stage15Model": row.get("stage1Model") or config.get("stage1Model") or "",
        "stage2Model": row.get("stage2Model") or config.get("stage2Model") or "",
        "pipelineMode": "replay",
    }
    path = scratch / f"meta-{day}.json"
    path.write_text(json.dumps(meta, indent=2))
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", required=True, help="folder name under verification/")
    ap.add_argument("--prefix", default="REPLAY", help="label prefix marking these as not real days")
    ap.add_argument("--style", default=None,
                    help="the style the batch was actually rendered in. Needed for batches "
                         "produced before replay_days recorded it, where the fallback is the "
                         "CONFIGURED style and will be wrong.")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    batch = VERIFY_ROOT / args.batch
    if not batch.is_dir():
        raise SystemExit(f"No such batch: {batch}")
    config = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    scratch = batch / ".archive-scratch"
    scratch.mkdir(exist_ok=True)

    pairs = []
    for png in sorted(batch.glob("*.png")):
        side = png.with_suffix(".json")
        if not side.exists():
            continue
        try:
            row = json.loads(side.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        m = re.search(r"(\d{4}-\d{2}-\d{2})", row.get("day") or png.stem)
        pairs.append((png, row, m.group(1) if m else None))

    if not pairs:
        raise SystemExit(f"No render+metadata pairs in {batch}")

    print(f"{len(pairs)} renders from '{args.batch}' -> the real archive")
    for png, row, day in pairs[:4]:
        print(f"  {day or '(no date)'}  {row.get('register','?')[:22]:22} {png.name}")
    if len(pairs) > 4:
        print(f"  ... and {len(pairs) - 4} more")
    print(f"\nEach is labelled '{args.prefix} · ...' and appears in the widget's")
    print("Themes Generated picker. Remove later with astro-arc-prune-history.")
    if not args.yes:
        print("\nDry run. Re-run with --yes.")
        return 0

    filed = 0
    for png, row, day in pairs:
        reading = chart_for(day, scratch)
        meta = meta_for(row, day, config, scratch, args.style)
        pretty = datetime.strptime(day, "%Y-%m-%d").strftime("%b %-d") if day else "undated"
        style = (args.style or row.get("artStyle") or config.get("artStyle") or "").title()
        label = f"{args.prefix} · {pretty} · {style} · {row.get('register','')}"
        cmd = [sys.executable, str(HERE / "build_review.py"),
               "--reading", str(reading), "--meta", str(meta), "--image", str(png),
               "--label", label, "--footer",
               f"Replayed from verification batch '{args.batch}'. Not a real generation: "
               f"no theme was set and the chart is recomputed for this date at noon local."]
        if day:
            cmd += ["--period-key", day]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"  FAILED {png.name}: {(proc.stderr or proc.stdout)[:180]}", file=sys.stderr)
            continue
        filed += 1
        print(f"  filed {label}")

    subprocess.run([sys.executable, str(HERE / "archive_gallery.py")], check=False)
    print(f"\n{filed}/{len(pairs)} filed; gallery rebuilt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
