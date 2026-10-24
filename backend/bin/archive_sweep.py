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

Each entry also gets a theme built from its render by the built-in generator,
so Set Theme works on it. Entries land in generations.json like any other and
can be removed with
astro-arc-prune-history, so this is reversible.

    python3 backend/bin/archive_sweep.py --batch day-variation --prefix "REPLAY"
    python3 backend/bin/archive_sweep.py --batch day-variation --yes
    python3 backend/bin/archive_sweep.py --batch cross-hard-cosmic --batch cross-soft-cosmic --yes

Two batch shapes are understood: replay_days output (one sidecar per day) and
sweep.py output (one reading, many registers, with 00_sweep.json beside it).
A sweep already carries its reading, chart and style, so nothing is recomputed.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from paths import CONFIG_FILE, PIPELINE_STATE_DIR, VERIFY_DIR  # noqa: E402

READINGS_DIR = PIPELINE_STATE_DIR / "readings"


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


def theme_for(png, dest):
    """A theme folder for one render, built the way astro-arc-generate's
    built-in generator builds one, so Set Theme and Save Selected Theme work
    on a filed render like on any real generation."""
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "backgrounds").mkdir(parents=True)
    shutil.copyfile(png, dest / "backgrounds" / "background.png")
    subprocess.run([sys.executable, str(HERE / "palette_extract.py"),
                    str(png), str(dest / "colors.toml")], check=True,
                   capture_output=True, text=True)
    return dest


def sweep_rows(batch):
    """sweep.py sidecars rewritten into replay_days row shape, plus the
    batch-wide reading. Returns (pairs, stage1, reading_path) or None if
    `batch` is not a sweep."""
    head_path = batch / "00_sweep.json"
    if not head_path.exists():
        return None
    head = json.loads(head_path.read_text())
    amp = head.get("amplification") or {}
    src = head.get("readingSource") or ""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", src) or re.search(r"(\d{4}-\d{2}-\d{2})", head.get("label") or "")
    day = m.group(1) if m else None
    pairs = []
    for png in sorted(batch.glob("[0-9]*.png")):
        side = png.with_suffix(".json")
        if not side.exists():
            continue
        s = json.loads(side.read_text())
        pairs.append((png, {
            "day": day,
            "artStyle": s.get("style") or head.get("style"),
            "register": s.get("primaryRegister") or "",
            "secondary": s.get("secondaryRegister") or "",
            "conceptTags": "|".join(s.get("conceptTags") or []),
            "constellation": s.get("constellation") or amp.get("constellation") or "",
            "movement": amp.get("movement") or "",
            "affect": amp.get("affect") or "",
            "amplificationObjects": s.get("objects") or [],
            "anomaly": s.get("anomaly") or "",
            "actor": amp.get("actor") or "",
            "actorKind": s.get("actorKind") or amp.get("actorKind") or "",
            "hasPeople": s.get("hasPeople"),
            "draws": s.get("draws") or {},
            "imagePrompt": s.get("imagePrompt") or "",
            "finalPrompt": s.get("finalPrompt") or "",
            "stage1Model": head.get("stage1Model") or "",
            "stage2Model": head.get("stage2Model") or "",
        }, day))
    stage1 = head.get("stage1") or {}
    if not stage1 and (batch / "00_stage1.json").exists():
        stage1 = json.loads((batch / "00_stage1.json").read_text())
    reading = Path(src) if src and Path(src).exists() else None
    return pairs, stage1, reading


def meta_for(row, day, config, scratch, style_override=None, stage1=None):
    """A pipeline-meta-shaped file from a replay row.

    Rows written before replay_days persisted the amplification have no
    constellation, movement, affect or object list. Those are left empty
    rather than invented; the review page renders a dash.
    """
    stage1 = stage1 or {}
    cached = READINGS_DIR / f"{day}.json"
    if not stage1 and cached.exists():
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
        "draws": row.get("draws") or {},
        "imagePrompt": row.get("imagePrompt") or "",
        "finalPrompt": row.get("finalPrompt") or row.get("imagePrompt") or "",
        "stage1Model": row.get("stage1Model") or config.get("stage1Model") or "",
        "stage15Model": row.get("stage1Model") or config.get("stage1Model") or "",
        "stage2Model": row.get("stage2Model") or config.get("stage2Model") or "",
        "pipelineMode": "replay",
    }
    path = scratch / f"meta-{day}-{row.get('register') or 'x'}-{row.get('secondary') or 'x'}.json"
    path.write_text(json.dumps(meta, indent=2))
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", required=True, action="append",
                    help="folder name under verification/; repeat for several")
    ap.add_argument("--prefix", default="REPLAY", help="label prefix marking these as not real days")
    ap.add_argument("--style", default=None,
                    help="the style the batch was actually rendered in. Needed for batches "
                         "produced before replay_days recorded it, where the fallback is the "
                         "CONFIGURED style and will be wrong.")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    config = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}

    jobs = []  # (batch name, png, row, day, stage1, reading path or None)
    for name in args.batch:
        batch = VERIFY_DIR / name
        if not batch.is_dir():
            raise SystemExit(f"No such batch: {batch}")
        sweep = sweep_rows(batch)
        if sweep is not None:
            pairs, stage1, reading = sweep
            jobs += [(name, png, row, day, stage1, reading) for png, row, day in pairs]
            continue
        for png in sorted(batch.glob("*.png")):
            side = png.with_suffix(".json")
            if not side.exists():
                continue
            try:
                row = json.loads(side.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            m = re.search(r"(\d{4}-\d{2}-\d{2})", row.get("day") or png.stem)
            jobs.append((name, png, row, m.group(1) if m else None, None, None))

    if not jobs:
        raise SystemExit(f"No render+metadata pairs in {', '.join(args.batch)}")

    print(f"{len(jobs)} renders from {len(args.batch)} batch(es) -> the real archive")
    for name, png, row, day, _, _ in jobs[:4]:
        print(f"  {day or '(no date)'}  {row.get('register','?')[:22]:22} {name}/{png.name}")
    if len(jobs) > 4:
        print(f"  ... and {len(jobs) - 4} more")
    print(f"\nEach is labelled '{args.prefix} · ...' and appears in the widget's")
    print("Themes Generated picker. Remove later with astro-arc-prune-history.")
    if not args.yes:
        print("\nDry run. Re-run with --yes.")
        return 0

    filed = 0
    for name, png, row, day, stage1, reading in jobs:
        scratch = VERIFY_DIR / name / ".archive-scratch"
        scratch.mkdir(exist_ok=True)
        how = "the chart is that day's own reading"
        if reading is None:
            reading = chart_for(day, scratch)
            how = "the chart is recomputed for this date at noon local"
        meta = meta_for(row, day, config, scratch, args.style, stage1)
        pretty = datetime.strptime(day, "%Y-%m-%d").strftime("%b %-d") if day else "undated"
        style = (args.style or row.get("artStyle") or config.get("artStyle") or "").title()
        register = row.get("register", "")
        if row.get("secondary"):
            register += " + " + row["secondary"]
        label = f"{args.prefix} · {pretty} · {style} · {register}"
        cmd = [sys.executable, str(HERE / "build_review.py"),
               "--reading", str(reading), "--meta", str(meta), "--image", str(png),
               "--label", label, "--footer",
               f"Replayed from verification batch '{name}'. Not a real generation: "
               f"no theme was set and {how}.",
               "--theme-dir", str(theme_for(png, scratch / "theme"))]
        if day:
            cmd += ["--period-key", day]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"  FAILED {png.name}: {(proc.stderr or proc.stdout)[:180]}", file=sys.stderr)
            continue
        filed += 1
        print(f"  filed {label}")

    subprocess.run([sys.executable, str(HERE / "archive_gallery.py")], check=False)
    print(f"\n{filed}/{len(jobs)} filed; gallery rebuilt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
