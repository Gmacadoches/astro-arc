#!/usr/bin/env python3
"""Verification sweep harness — runs the prompt pipeline across every
register (or register pair) against a *fixed* Stage 1 reading, so the only
thing varying between samples is the thing under test.

Why this exists as a committed script rather than an ad-hoc run: this
project's own history (see CONTEXT.md, "A style is either a technique or a
world") records that 2-4-sample spot checks let a real content bug ship
three times in a row, and that the one comparison batch that would have
caught it "was never saved." A sweep needs to be cheap enough to re-run on
every change and self-archiving, or it doesn't get run.

It deliberately does NOT go through astro-arc-generate or llm_pipeline's
own build(): it calls the stage functions directly so it can
  - pin Stage 1 (one reading shared by every sample — otherwise register
    differences are confounded with reading differences),
  - neutralize the avoid_concepts window so register coverage is actually
    even instead of being steered by whatever ran recently,
  - and, most importantly, touch none of the production state:
    history.json, the readings cache, last-run.json, costs.json and the
    live theme are all left exactly as they were. A sweep must never
    perturb the series it is measuring.

Usage:
    sweep.py --out <name> [--registers all|a,b,c] [--pairs] [--style ghibli]
             [--reading <reading.json>] [--stage1 <stage1.json>]
             [--stage2-model M] [--stage1-model M]
             [--no-image] [--quality medium] [--cell CELL] [--label TEXT]

Output goes to ~/.local/state/omarchy/astro-arc/verification/<name>/:
    00_sweep.json          every prompt, register, tag set and cost
    00_summary.md          human-readable index
    NN_<register>.png      the render
    NN_<register>.json     that sample's full metadata
"""

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import llm_pipeline as lp  # noqa: E402
from openai_image_gen import generate as openai_generate  # noqa: E402

VERIFY_ROOT = Path.home() / ".local/state/omarchy/astro-arc/verification"
CONFIG_PATH = Path.home() / ".local/state/omarchy/settings/astro-arc.json"
STATE_DIR = Path.home() / ".local/state/omarchy/astro-arc"


def load_config():
    return json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}


def latest_reading():
    """Most recent real reading-*.json — the sweep's default fixed input, so
    samples are judged against a reading the user has actually seen output
    for rather than a synthetic one."""
    candidates = sorted(STATE_DIR.glob("reading-*.json"))
    if not candidates:
        raise SystemExit("No reading-*.json found; pass --reading explicitly.")
    return candidates[-1]


def slug(text):
    return "".join(c if c.isalnum() else "_" for c in text.lower()).strip("_")


# Words that carry none of an anomaly's identity. Most are the boilerplate the
# "older, and only older" rule makes Stage 1.5 append to nearly every one
# ("far older and more worn than everything around it"), so leaving them in
# would score an anomaly as surviving on the strength of the word "older".
_ANOMALY_STOPWORDS = {
    "older", "oldest", "much", "more", "most", "worn", "wear", "than", "that",
    "this", "with", "from", "into", "onto", "over", "under", "around", "about",
    "other", "others", "objects", "object", "thing", "things", "everything",
    "anything", "nearly", "almost", "still", "been", "being", "here", "there",
    "present", "centuries", "century", "ages", "long", "longer", "smoothed",
    "smooth", "featureless", "scale", "sized", "small", "large", "larger",
    "kind", "same", "very", "deeply", "rough", "rougher", "surface", "erased",
    "them", "they", "their", "which", "where", "what", "when", "all", "and",
    "the", "for", "its", "it's", "far", "too", "not", "but",
}


def anomaly_tokens(anomaly):
    """The words that actually identify an anomaly, for survival scoring.

    An anomaly reads like "a Neolithic polished stone palette, surface worn
    nearly smooth and all markings erased through centuries of handling". Only
    a few of those words name the thing; the rest are the age boilerplate. Four
    characters or more, not a stopword, deduped in order.
    """
    words = re.findall(r"[a-z]{4,}", (anomaly or "").lower())
    out = []
    for w in words:
        if w not in _ANOMALY_STOPWORDS and w not in out:
            out.append(w)
    return out


def anomaly_survival(anomaly, prompt):
    """(hits, total, survived) — did the anomaly reach the final prompt?

    STAGE2_SYSTEM makes the anomaly mandatory, and the composition brief also
    forbids flagging it with "incongruously", "unmistakably" and the rest. Both
    pressures push toward quietly dropping it, and until now nothing measured
    whether it arrived. Matching is prefix-based so "menhirs" still counts as
    "menhir"; `survived` is deliberately generous at a single hit, because one
    identifying noun is enough to place the object in the scene.
    """
    tokens = anomaly_tokens(anomaly)
    if not tokens:
        return 0, 0, None
    low = (prompt or "").lower()
    hits = sum(1 for t in tokens if t[:5] in low)
    return hits, len(tokens), hits >= 1


# Every polarity x intensity cell once, exposure lo/mid/hi twice each and
# multiplicity across its range, so six consecutive samples cover the dial.
ROTATION_CELLS = (
    "hard/hi/4/lo",
    "soft/lo/1/hi",
    "hard/mid/2/mid",
    "soft/mid/3/lo",
    "hard/lo/1/hi",
    "soft/hi/4/mid",
)


def texture_for_cell(cell):
    pol, iband, mult, eband = cell.split("/")
    return {"polarity": pol, "intensityBand": iband,
            "multiplicity": int(mult), "exposureBand": eband}


def dial_for_day(state_dir, key, cell):
    """A replayed day's dial and where its base came from: (dial, source).

    The base is dial_from_texture on that day's own texture -- the copy
    stored in its pipeline-meta, else its reading-<key>.json -- so the cell
    is the one that day really had. A day from before the texture existed
    (or with a broken one) has none, and its base is built from `cell`
    instead (source "cell"). Whatever dial the pipeline-meta stored then goes
    on top, as before. Production never does this: a reading without its
    texture is an error there. Shared by replay_days.py and replay_amplify.py.
    """
    import llm_pipeline as lp  # the harness may be pointed at another build

    def read(path):
        try:
            return json.loads(path.read_text()) if path.exists() else {}
        except (json.JSONDecodeError, OSError):
            return {}

    meta = read(state_dir / f"pipeline-meta-{key}.json")
    dial, source = None, "cell"
    for texture in (meta.get("texture"),
                    (read(state_dir / f"reading-{key}.json").get("arc") or {}).get("texture")):
        try:
            dial, source = lp.dial_from_texture(texture), "texture"
            break
        except lp.PipelineError:
            continue
    if dial is None:
        dial = lp.dial_from_texture(texture_for_cell(cell))
    dial.update({k: v for k, v in (meta.get("dial") or {}).items() if v is not None})
    return dial, source


def report_filled(sources, cell):
    """One note for the days that had no usable texture of their own."""
    from_cell = [k for k, src in sources if src == "cell"]
    if from_cell:
        print(f"note: {len(from_cell)} of {len(sources)} day(s) have no usable texture "
              f"({', '.join(from_cell)}); their base dial is --cell {cell}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="folder name under verification/")
    ap.add_argument("--registers", default="all", help="'all' or a comma-separated subset")
    ap.add_argument("--pairs", action="store_true", help="sweep register pairs (primary+secondary) instead of singles")
    ap.add_argument("--style", default=None, help="art style key (defaults to config)")
    ap.add_argument("--reading", default=None, help="reading-*.json to pin (defaults to newest)")
    ap.add_argument("--stage1", default=None, help="pre-baked Stage 1 JSON to pin instead of computing one")
    ap.add_argument("--stage1-model", default=None)
    ap.add_argument("--stage2-model", default=None)
    ap.add_argument("--quality", default=None,
                    help="image tier for every sample (default: the config's openaiQuality, as production)")
    ap.add_argument("--image-model", default=None)
    ap.add_argument("--size", default=None, help="WxH, defaults to config backgroundSize")
    ap.add_argument("--no-image", action="store_true", help="prompts only — free, for wording iteration")
    # --cell pins one dial cell so it can be held constant while registers vary
    # (and vice versa) — the two matrices that actually prove the dial works.
    ap.add_argument("--cell",
                    help="pin one dial cell: polarity/intensityBand/multiplicity/exposureBand (e.g. soft/lo/1/hi)")
    # --rotate-cells gives each sample its own cell from ROTATION_CELLS instead.
    # PIPELINE.md's "two traps": a pinned cell repeats its own sentences across
    # every sample and reads as a style problem. Production rotates cells
    # daily, so a variety measurement must too.
    ap.add_argument("--rotate-cells", action="store_true",
                    help="give each sample the next cell of ROTATION_CELLS instead of one pinned dial")
    ap.add_argument("--repeat", type=int, default=1,
                    help="run the register list this many times (cells keep rotating)")
    ap.add_argument("--force-quality", action="store_true",
                    help="no-op since 2026-10-08; every sample already uses one tier")
    ap.add_argument("--label", default="", help="one line recorded in the summary")
    args = ap.parse_args()

    config = load_config()
    if args.stage1_model:
        config["stage1Model"] = args.stage1_model
    if args.stage2_model:
        config["stage2Model"] = args.stage2_model
    style_key = args.style or config.get("artStyle") or "symbolist"
    if hasattr(lp, "require_charter"):
        lp.require_charter(style_key)
    image_model = args.image_model or config.get("openaiModel") or "gpt-image-1-mini"
    size = args.size or config.get("backgroundSize") or "1600x900"
    width, height = (int(x) for x in size.split("x"))

    stage1_model = config.get("stage1Model") or "gpt-4o-mini"
    stage2_model = config.get("stage2Model") or "gpt-4o-mini"

    reading_path = Path(args.reading) if args.reading else latest_reading()
    reading = json.loads(reading_path.read_text())

    out_dir = VERIFY_ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- pin the dial ------------------------------------------------
    # Pinned once and shared by every sample, for the same reason Stage 1 is: otherwise register differences are confounded with dial
    # differences and the sweep proves nothing.
    dial = None
    if hasattr(lp, "dial_from_texture"):
        if args.cell:
            texture = texture_for_cell(args.cell)
        else:
            texture = reading["arc"].get("texture")
        dial = lp.dial_from_texture(texture)
        (out_dir / "00_dial.json").write_text(json.dumps(
            {"cell": args.cell, "rotateCells": list(ROTATION_CELLS) if args.rotate_cells else None,
             "texture": texture, "dial": dial},
            indent=2, default=str))

    # --- pin Stage 1 -------------------------------------------------
    if args.stage1:
        stage1 = json.loads(Path(args.stage1).read_text())
        stage1_cost = None
    else:
        period_key = reading["arc"]["periodKey"]
        stage1, stage1_cost = lp.stage1_interpret(reading, stage1_model, period_key, config)
    (out_dir / "00_stage1.json").write_text(json.dumps(stage1, indent=2))


    # --- Stage 1.5, if this build of the pipeline has one -------------
    amplification = None
    amp_cost = None
    # As of 2026-09-26 Stage 1.5 is two calls: the MEANING, fixed per day, and
    # the MATERIAL, fresh per register. A sweep mirrors production exactly by
    # drawing the meaning ONCE (below) and the material per register (in the
    # loop) -- which is also what isolates the register as the only variable.
    # Recency lists are off (`[]`): production history must not steer a sweep.
    split = hasattr(lp, "stage15_material")
    if split:
        amplification, amp_cost = lp.stage15_meaning(
            stage1, stage1_model, reading["arc"]["periodKey"], config, cache=False,
            dial=dial, avoid_archetypes=[], avoid_kinds=[],
            style_key=style_key, style_world=lp.load_style_world(style_key))
        (out_dir / "00_amplification.json").write_text(json.dumps(amplification, indent=2))
    elif hasattr(lp, "stage15_amplify"):
        amp_kwargs = {"cache": False}
        if dial is not None:
            amp_kwargs["dial"] = dial
        # The style is a Stage 1.5 input as of 2026-09-20 — objects and the
        # anomaly are named in the world's terms — so a sweep that omits it
        # would measure a pipeline production never runs. Guarded by hasattr
        # so the harness still works against an older checkout.
        if hasattr(lp, "load_style_world"):
            amp_kwargs["style_key"] = style_key
            amp_kwargs["style_world"] = lp.load_style_world(style_key)
        try:
            amplification, amp_cost = lp.stage15_amplify(
                stage1, stage1_model, reading["arc"]["periodKey"], config, **amp_kwargs
            )
        except TypeError:
            # Pre-dial pipeline — still produce a baseline.
            amplification, amp_cost = lp.stage15_amplify(
                stage1, stage1_model, reading["arc"]["periodKey"], config, cache=False
            )
        (out_dir / "00_amplification.json").write_text(json.dumps(amplification, indent=2))

    registers = lp.load_registers()
    if args.registers != "all":
        wanted = [r.strip() for r in args.registers.split(",")]
        registers = [r for r in registers if r in wanted] or wanted

    registers = registers * max(1, args.repeat)
    job_cells = [ROTATION_CELLS[i % len(ROTATION_CELLS)] if args.rotate_cells else None
                 for i in range(len(registers))]
    job_dials = [lp.dial_from_texture(texture_for_cell(c)) if c else dial for c in job_cells]

    if args.pairs:
        # Mirror production's pairing rules (llm_pipeline.pick_registers)
        # rather than pairing naively: the secondary must never be a people
        # register (people are gated on the primary alone) and should come
        # from a different material family, or the pair collapses back into
        # one material. Deterministic offset-walk instead of a random pick
        # so a sweep is reproducible.
        people = getattr(lp, "PEOPLE_REGISTERS", set())
        families = getattr(lp, "REGISTER_FAMILIES", {})
        # Honor the dial's cohesion, or a cohere cell would be tested with
        # colliding pairs and prove nothing about the mode it claims to test.
        jobs = []
        for i, primary in enumerate(registers):
            cohesion = (job_dials[i] or {}).get("registerCohesion", "collide")
            pool = [r for r in registers if r != primary and r not in people]
            if cohesion == "cohere":
                # Same family, and genuinely no partner where a family has no
                # other member — matching production, where that correctly
                # yields no secondary at all. Since the 2026-09-13 expansion
                # that is only the human registers, which are excluded from the
                # pool above anyway.
                pool = [r for r in pool if families.get(r) == families.get(primary)]
            else:
                distant = [r for r in pool if families.get(r) != families.get(primary)]
                pool = distant or pool
            jobs.append((primary, pool[i % len(pool)] if pool else None))
    else:
        jobs = [(r, None) for r in registers]

    cliches = lp.load_cliches()
    style_world = lp.load_style_world(style_key)

    results = []
    total_cost = 0.0
    # The sweep's own history, for the per-style draws: each sample sees the
    # draws of the samples before it, exactly as consecutive production runs
    # do, and production's history.json is never read or written.
    sweep_history = []
    period_key = reading["arc"]["periodKey"]
    for i, (primary, secondary) in enumerate(jobs, start=1):
        dial = job_dials[i - 1]
        draws = {}
        history_entry = {"artStyle": style_key}
        if dial is not None and hasattr(lp, "draw_style_choices"):
            dial, draws = lp.draw_style_choices(style_key, dial, sweep_history, period_key)
            history_entry["draws"] = draws
        name = slug(primary) + ("__" + slug(secondary) if secondary else "")
        stem = f"{i:02d}_{name}"
        print(f"[{i}/{len(jobs)}] {primary}" + (f" + {secondary}" if secondary else ""), file=sys.stderr)

        kwargs = {}
        if secondary is not None:
            kwargs["secondary_register"] = secondary
        sample_amp = amplification
        material_cost = None
        if split and amplification is not None:
            material_kwargs = {}
            if hasattr(lp, "recent_material"):
                material_kwargs["avoid_material"] = lp.recent_material(sweep_history)
            material, material_cost = lp.stage15_material(
                stage1, amplification, stage1_model, config, dial=dial,
                style_key=style_key, style_world=lp.load_style_world(style_key),
                register=primary, secondary_register=secondary, **material_kwargs)
            sample_amp = {**amplification, **material}
            history_entry["objects"] = material.get("objects") or []
            history_entry["highlight"] = material.get("anomaly") or ""
            total_cost += material_cost or 0.0
        if sample_amp is not None:
            kwargs["amplification"] = sample_amp
        if dial is not None:
            kwargs["dial"] = dial
        # Production passes these (see llm_pipeline.build); without them a
        # sweep measures a prompt production never sends. Guarded by hasattr
        # so the harness still runs against an older build of the pipeline,
        # same as the kwargs above.
        if hasattr(lp, "load_register_line"):
            guidance = lp.load_register_line(style_key, primary)
            sec_guidance = lp.load_register_line(style_key, secondary)
            if sec_guidance:
                kwargs["secondary_register_guidance"] = sec_guidance
        elif hasattr(lp, "load_register_guidance"):
            guidance = lp.load_register_guidance(primary)
        else:
            guidance = ""
        if guidance:
            kwargs["register_guidance"] = guidance
        if hasattr(lp, "load_style_brief"):
            # Production passes the style's own brief rules (see build());
            # stage2_image_prompt refuses to run without them.
            kwargs["style_brief"] = lp.load_style_brief(style_key)
        if hasattr(lp, "load_style_highlight"):
            kwargs["highlight"] = lp.load_style_highlight(style_key)

        try:
            prompt, tags, has_people, s2cost = lp.stage2_image_prompt(
                stage1, primary, [], cliches, stage2_model,
                style_world, **kwargs
            )
        except TypeError:
            # Older pipeline signature (pre-Task 3/4) — fall back so the
            # harness can still produce a baseline on unmodified code.
            prompt, tags, has_people, s2cost = lp.stage2_image_prompt(
                stage1, primary, [], cliches, stage2_model,
                style_world,
            )

        suffix = lp.load_style_suffix(style_key, has_people)
        final_prompt = f"{prompt}, {suffix}" if suffix else prompt

        # One tier for every sample, the same one production uses. This used to
        # bump figures to "high" and default everything else to "medium", on the
        # premise that "production renders figures at the high tier" -- true
        # until 2026-09-13, when that bump was removed from production. Since
        # then sweeps rendered figure and non-figure frames at different tiers
        # from each other AND from production. --force-quality is kept so old
        # command lines still parse; it no longer changes anything.
        quality = args.quality or config.get("openaiQuality") or "medium"
        record = {
            "index": i,
            "primaryRegister": primary,
            "secondaryRegister": secondary,
            "style": style_key,
            "imagePrompt": prompt,
            "finalPrompt": final_prompt,
            "conceptTags": tags,
            "hasPeople": has_people,
            "promptWords": len(prompt.split()),
            "cell": job_cells[i - 1] or args.cell,
            "draws": draws,
            "stage2Cost": s2cost,
            "quality": quality,
        }
        if hasattr(lp, "caption_check"):
            # Record-only: production resamples Stage 2 on a failure (see
            # build()), but a sweep records the verdict on the first draw so it
            # measures how often production WOULD resample, instead of hiding it.
            caption, hits, passed = lp.caption_check(
                prompt, config.get("captionModel") or getattr(lp, "CAPTION_MODEL", "gpt-4o-mini"))
            record["caption"] = caption
            record["captionHits"] = hits
            record["captionPassed"] = passed
        if sample_amp is not None:
            # Per sample, because the objects and the anomaly now differ per
            # register; the constellation is recorded too so a sweep shows it.
            record["constellation"] = sample_amp.get("constellation")
            record["actorKind"] = sample_amp.get("actorKind")
            record["objects"] = sample_amp.get("objects")
            record["anomaly"] = sample_amp.get("anomaly")
            record["materialCost"] = material_cost

        # Did the mandatory anomaly actually reach the prompt? Stage 2 is told
        # it is mandatory and also told not to flag it with any of the words
        # that would make it obvious, and both pressures push toward dropping
        # it quietly. Recorded per sample so a sweep can show the rate.
        if sample_amp is not None:
            hits, total_tokens, survived = anomaly_survival(
                sample_amp.get("anomaly"), prompt)
            record["anomalyTokenHits"] = hits
            record["anomalyTokens"] = total_tokens
            record["anomalySurvived"] = survived
        total_cost += s2cost or 0.0

        if not args.no_image:
            for attempt in range(3):
                try:
                    _, cost_info = openai_generate(
                        final_prompt, out_dir / f"{stem}.png", image_model, quality, width, height
                    )
                    record["imageCost"] = cost_info.get("cost")
                    total_cost += cost_info.get("cost") or 0.0
                    break
                except Exception as exc:  # noqa: BLE001 — a sweep must survive one bad render
                    record["imageError"] = str(exc)[:300]
                    print(f"    render failed ({attempt + 1}/3): {str(exc)[:160]}", file=sys.stderr)
                    time.sleep(4 * (attempt + 1))

        (out_dir / f"{stem}.json").write_text(json.dumps(record, indent=2))
        results.append(record)
        sweep_history.insert(0, history_entry)

    summary = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "label": args.label,
        "style": style_key,
        "stage1Model": stage1_model,
        "stage2Model": stage2_model,
        "imageModel": None if args.no_image else image_model,
        "readingSource": str(reading_path),
        "pairs": args.pairs,
        "stage1": stage1,
        "amplification": amplification,
        "stage1Cost": stage1_cost,
        "stage15Cost": amp_cost,
        "totalCost": round(total_cost, 4),
        "samples": results,
    }
    (out_dir / "00_sweep.json").write_text(json.dumps(summary, indent=2))

    # Flat CSV beside the JSON. The JSON is the record; this is the thing you
    # can actually diff two batches with, which 00_summary.md and a folder of
    # per-sample JSON are not. One row per sample, the run's identity repeated
    # on every row so several sweeps concatenate into one table.
    csv_path = out_dir / "00_sweep.csv"
    columns = [
        "out", "label", "style", "index", "primaryRegister", "secondaryRegister",
        "hasPeople", "promptWords", "conceptTags",
        "anomalySurvived", "anomalyTokenHits", "anomalyTokens",
        "caption", "captionPassed", "captionHits",
        "quality", "stage2Cost", "imageCost", "imageError",
    ]
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            row = dict(r)
            row["out"] = args.out
            row["label"] = args.label
            row["conceptTags"] = "|".join(r.get("conceptTags") or [])
            row["captionHits"] = "|".join(r.get("captionHits") or [])
            writer.writerow(row)

    survived = [r.get("anomalySurvived") for r in results if r.get("anomalySurvived") is not None]
    words = [r["promptWords"] for r in results]
    if words:
        print(f"prompt words: min {min(words)} / median "
              f"{sorted(words)[len(words) // 2]} / max {max(words)}", file=sys.stderr)
    if survived:
        print(f"anomaly survived: {sum(survived)}/{len(survived)} "
              f"({sum(survived) / len(survived):.0%})", file=sys.stderr)

    lines = [f"# Sweep: {args.out}", ""]
    if args.label:
        lines += [f"_{args.label}_", ""]
    lines += [
        f"- style: `{style_key}`  |  stage2: `{stage2_model}`  |  pairs: {args.pairs}",
        f"- distillation: {stage1.get('distillation')}",
        f"- narrative position: {stage1.get('narrativePosition')}",
        f"- total cost: ${summary['totalCost']}",
        "",
    ]
    for r in results:
        reg = r["primaryRegister"] + (f" + {r['secondaryRegister']}" if r["secondaryRegister"] else "")
        lines += [
            f"## {r['index']:02d}. {reg}",
            f"`{r['promptWords']} words` · hasPeople={r['hasPeople']} · tags: {', '.join(r['conceptTags'])}",
            "",
            r["imagePrompt"],
            "",
        ]
    (out_dir / "00_summary.md").write_text("\n".join(lines))

    print(f"\nSweep written to {out_dir}  (${summary['totalCost']})", file=sys.stderr)
    print(out_dir)


if __name__ == "__main__":
    main()
