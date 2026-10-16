#!/usr/bin/env python3
"""Which unchartered-style fallbacks does production still reach?

Drives llm_pipeline.build() (the real code path) for every shipped style,
every dial cell (polarity hard/soft/none x intensity lo/mid/hi x exposure
lo/mid/hi, plus a reading with no texture) and every register as primary, once
with no secondary and once with the next register as secondary. Every model
call goes to a stub that returns a fixed response, so nothing is billed, and
the Stage 2 user prompt each run would have sent is captured.

Written for docs/decisions/2026-10-styles.md decisions 2 and 4: prove what is
reachable before anything is deleted, and keep a baseline that later changes
can be diffed against.

Output, under ~/.local/state/omarchy/astro-arc/verification/<out>/:
  prompts.jsonl   one line per run, in a fixed order: style, cell, registers,
                  and the Stage 2 user prompt
  prompts.sha256  sha256 of prompts.jsonl, the whole set in one hash
  fallbacks.json  every fallback that fired, with the styles it fired for
  control.jsonl   the same capture for a synthetic style with no charter at
                  all; a positive control that proves each detector can fire.
                  Not part of the hash.

Pipeline state (history, the Stage 1 and 1.5 caches) is redirected to a
temporary directory, so the real archive is neither read nor written.

load_styles() and _load_actors() are cached for the run by default. Both
re-parse their TOML on every call (actors.toml about 24 times per build()),
and uncached a single style takes over two minutes. --no-cache turns it off.

    fallback_audit.py --out baseline
"""

import argparse
import hashlib
import json
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import llm_pipeline as lp  # noqa: E402

VERIFY_ROOT = Path.home() / ".local/state/omarchy/astro-arc/verification"

PERIOD_KEY = "audit-2026-10-16"
CONFIG = {"stage1Model": "stub-model", "stage2Model": "stub-model", "captionModel": "stub-model"}
POLARITIES = ("hard", "soft", "none")
BANDS = ("lo", "mid", "hi")
CONTROL_STYLE = "__unchartered__"

# Fixed fake responses by pipeline stage. Stage 1.5's meaning and material
# halves both run as stage="stage15", so one response carries both halves' keys.
STUB_RESPONSES = {
    "stage1": {"reading": "STUB reading.", "distillation": "STUB distillation.",
               "narrativePosition": "threshold"},
    "stage15": {"constellation": "STUB constellation", "movement": "STUB movement",
                "affect": "STUB affect", "actorKind": "solitary", "actor": "STUB actor",
                "objects": ["STUB object one", "STUB object two", "STUB object three"],
                "anomaly": "STUB highlight", "anomalyType": "scale"},
    "stage2": {"prompt": "STUB prompt", "conceptTags": [], "hasPeople": False},
    "caption": {"caption": "a stub scene", "motive": False, "sides": False, "performed": False},
}


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

class Audit:
    def __init__(self):
        self.style = None
        self.fired = defaultdict(lambda: defaultdict(int))  # "symbol: how" -> style -> count
        self.captured = []  # Stage 2 user prompts for the current build()
        self.chat_calls = defaultdict(int)

    def hit(self, symbol, how):
        self.fired[f"{symbol}: {how}"][self.style] += 1


AUDIT = Audit()


def _wrap(name, after):
    """Replace lp.<name> with a wrapper that calls the original, then
    after(result, *args, **kwargs). Module globals are looked up at call time,
    so every caller inside llm_pipeline goes through the wrapper."""
    original = getattr(lp, name)

    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        after(result, *args, **kwargs)
        return result

    setattr(lp, name, wrapper)


def _style(style_key):
    return lp.load_styles().get(style_key) or {}


def install_detectors():
    original_world = lp.load_style_world

    # --- load_style_brief: the unchartered branch and the default signature.
    # _default_signature is only ever called from load_style_brief, so which
    # branch called it follows from whether the style has a [brief] table.
    def after_brief(result, style_key):
        if not isinstance(_style(style_key).get("brief"), dict):
            AUDIT.hit("DEFAULT_STAGE2_BRIEF", "load_style_brief, style has no [brief]")
        if _space_skip_active(style_key):
            AUDIT.hit("SPACE_REPLACES_BRIEF", "load_style_brief skipped openness/depth (style has [space])")
    _wrap("load_style_brief", after_brief)

    def after_sig(result, style_key):
        brief = _style(style_key).get("brief")
        how = ("load_style_brief, unchartered branch" if not isinstance(brief, dict)
               else "load_style_brief, [brief] has no key in SIGNATURE_KEYS")
        AUDIT.hit("DEFAULT_SIGNATURE/_default_signature", how)
    _wrap("_default_signature", after_sig)

    # --- the highlight
    def after_highlight(result, style_key):
        if result is lp.DEFAULT_HIGHLIGHT:
            AUDIT.hit("DEFAULT_HIGHLIGHT", "load_style_highlight, charter has no highlight")
    _wrap("load_style_highlight", after_highlight)

    # --- verbs, conditions, world, info
    _wrap("_global_verbs", lambda result: AUDIT.hit(
        "_global_verbs", "draw_style_choices, style has no [verbs]"))

    def after_conditions(result, style_key):
        if result is None:
            AUDIT.hit("CONDITIONS", "draw_style_choices, style has no `conditions` (global wear scale)")
    _wrap("load_style_conditions", after_conditions)

    def after_world(result, style_key):
        if not result:
            AUDIT.hit("load_style_world", 'returned "" (no [world] charter)')
    _wrap("load_style_world", after_world)

    def after_info(result, style_key):
        styles = lp.load_styles()
        if style_key not in styles:
            AUDIT.hit("load_style_info", "unknown key, fell back to symbolist")
        key = style_key if style_key in styles else "symbolist"
        if not original_world(key):
            how = ("charter missing, `guidance` used" if (styles.get(key) or {}).get("guidance")
                   else "charter missing, no `guidance` either: style line dropped")
            AUDIT.hit("load_style_info", how)
    _wrap("load_style_info", after_info)

    def after_suffix(result, style_key, has_people=False):
        if style_key not in lp.load_styles():
            AUDIT.hit("load_style_suffix", "unknown key, fell back to symbolist")
    _wrap("load_style_suffix", after_suffix)

    # --- the dial
    def after_dial(result, texture):
        if not texture:
            AUDIT.hit("NO_TEXTURE_DIAL", "dial_from_texture, no texture")
            return
        if texture.get("polarity") == "none":
            AUDIT.hit("dial_from_texture", 'polarity "none" read as soft')
        for key in ("polarity", "intensityBand", "exposureBand"):
            if not texture.get(key):
                AUDIT.hit("dial_from_texture", f"missing {key}, `or` default used")
        AUDIT.hit("_VERBS_*/_ACTOR_VERBS_*", "dial_from_texture seeds dial verbs from the globals")
        AUDIT.hit("_LIGHT_BY_EXPOSURE", "dial_from_texture seeds dial light from the global table")
    _wrap("dial_from_texture", after_dial)

    original_draw = lp.draw_style_choices

    def draw(style_key, dial, history, period_key):
        if not dial.get("polarity"):
            AUDIT.hit("draw_style_choices", 'dial has no polarity, `or "soft"` used')
        return original_draw(style_key, dial, history, period_key)
    lp.draw_style_choices = draw

    for name in ("stage15_meaning", "stage15_material"):
        original_stage = getattr(lp, name)

        def stage15(*args, _name=name, _original=original_stage, **kwargs):
            if kwargs.get("dial") is None:
                AUDIT.hit("NO_TEXTURE_DIAL", f"{_name}, called with dial=None")
            return _original(*args, **kwargs)
        setattr(lp, name, stage15)

    # --- Stage 2: the `is None` / `or` fallbacks, and which globals actually
    # reach the prompt after draw_style_choices has had its turn.
    original_stage2 = lp.stage2_image_prompt

    def stage2(*args, **kwargs):
        dial = kwargs.get("dial")
        if dial is None:
            AUDIT.hit("NO_TEXTURE_DIAL", "stage2_image_prompt, called with dial=None")
        if kwargs.get("style_brief") is None:
            AUDIT.hit("DEFAULT_STAGE2_BRIEF", "stage2_image_prompt, style_brief is None")
        if not kwargs.get("highlight"):
            AUDIT.hit("DEFAULT_HIGHLIGHT", "stage2_image_prompt, `highlight or DEFAULT_HIGHLIGHT`")
        d = dial or lp.NO_TEXTURE_DIAL
        if not d.get("prominence"):
            AUDIT.hit("stage2_image_prompt", "`prominence or _PROMINENCE_BY_INTENSITY['mid']`")
        if d.get("verbs") in (lp._VERBS_HARD, lp._VERBS_SOFT):
            AUDIT.hit("_VERBS_*/_ACTOR_VERBS_*", "global process verbs reached Stage 2 verbatim")
        if d.get("actorVerbs") in (*lp._ACTOR_VERBS_HARD.values(), *lp._ACTOR_VERBS_SOFT.values()):
            AUDIT.hit("_VERBS_*/_ACTOR_VERBS_*", "global actor verbs reached Stage 2 verbatim")
        if d.get("verbs") == lp.NO_TEXTURE_DIAL["verbs"]:
            AUDIT.hit("NO_TEXTURE_DIAL", "its fixed verbs reached Stage 2")
        if d.get("light") and d.get("light") in lp._LIGHT_BY_EXPOSURE.values():
            AUDIT.hit("_LIGHT_BY_EXPOSURE", "global light line reached Stage 2 (style has no [light] line for the band)")
        if not d.get("light"):
            AUDIT.hit("stage2_image_prompt", "no light line at all")
        if d.get("condition") in lp.CONDITIONS and not _style(AUDIT.style).get("conditions"):
            AUDIT.hit("CONDITIONS", "global wear scale reached Stage 2")
        return original_stage2(*args, **kwargs)
    lp.stage2_image_prompt = stage2


def _space_skip_active(style_key):
    brief = _style(style_key).get("brief")
    return (isinstance(brief, dict) and bool(lp.load_style_space(style_key))
            and any(k in brief for k in lp.SPACE_REPLACES_BRIEF))


# ---------------------------------------------------------------------------
# Isolation: no network, no real state, no bill
# ---------------------------------------------------------------------------

def install_stubs(state_dir, cache_styles, control_style):
    def chat(model, system_prompt, user_prompt, slot, temperature=0.8, stage=None):
        AUDIT.chat_calls[stage] += 1
        if stage == "stage2":
            AUDIT.captured.append(user_prompt)
            AUDIT.stage2_system = system_prompt
        return json.loads(json.dumps(STUB_RESPONSES[stage])), None
    lp._chat_json = chat

    def no_network(*_a, **_k):
        raise RuntimeError("fallback_audit: network access attempted")
    lp.urllib.request.urlopen = no_network
    lp.get_api_key = no_network

    lp.STATE_DIR = state_dir
    lp.HISTORY_FILE = state_dir / "history.json"
    lp.READINGS_DIR = state_dir / "readings"
    lp.AMPLIFICATIONS_DIR = state_dir / "amplifications"
    lp.load_history = lambda: []
    lp.save_history_entry = no_network

    # The registers are the variable under test, so build() is handed them.
    lp.pick_registers = lambda history, registers, cohesion="collide": AUDIT.registers

    original_load = lp.load_styles
    cached = {}

    def load_styles():
        if cache_styles and "styles" in cached:
            return cached["styles"]
        styles = original_load()
        if control_style:
            styles[CONTROL_STYLE] = {"label": "Unchartered Control", "suffix": "control suffix"}
        cached["styles"] = styles
        return styles
    lp.load_styles = load_styles

    if cache_styles:
        actors = lp._load_actors()
        lp._load_actors = lambda: actors


def seed_stage1_cache(config):
    lp.READINGS_DIR.mkdir(parents=True, exist_ok=True)
    entry = dict(STUB_RESPONSES["stage1"], natalHash=lp._natal_hash(config),
                 schemaVersion=lp.STAGE1_SCHEMA_VERSION)
    (lp.READINGS_DIR / f"{PERIOD_KEY}.json").write_text(json.dumps(entry))


# ---------------------------------------------------------------------------
# The grid
# ---------------------------------------------------------------------------

def cells():
    for polarity in POLARITIES:
        for intensity in BANDS:
            for exposure in BANDS:
                yield (f"{polarity}/{intensity}/{exposure}",
                       {"polarity": polarity, "intensityBand": intensity,
                        "exposureBand": exposure, "multiplicity": 1})
    yield "no-texture", None


def register_pairs(registers):
    for i, primary in enumerate(registers):
        yield primary, None
        yield primary, registers[(i + 1) % len(registers)]


def run_style(style_key, registers, out):
    AUDIT.style = style_key
    config = dict(CONFIG, artStyle=style_key)
    for cell_name, texture in cells():
        for primary, secondary in register_pairs(registers):
            # Stage 1.5's cache would otherwise serve one cell's meaning to the
            # next, and its directory feeds the recent-constellation lists.
            for path in lp.AMPLIFICATIONS_DIR.glob("*.json"):
                path.unlink()
            AUDIT.registers = (primary, secondary)
            AUDIT.captured = []
            reading = {"arc": {"periodKey": PERIOD_KEY, "frequency": "daily", "texture": texture}}
            lp.build(reading, config)
            if len(AUDIT.captured) != 1:
                raise SystemExit(f"{style_key} {cell_name}: expected one Stage 2 call, "
                                 f"got {len(AUDIT.captured)}")
            out.write(json.dumps({"style": style_key, "cell": cell_name, "primary": primary,
                                  "secondary": secondary, "prompt": AUDIT.captured[0]},
                                 sort_keys=True, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, help="name of the verification folder")
    parser.add_argument("--styles", nargs="*", help="only these style keys (default: all shipped)")
    parser.add_argument("--no-cache", action="store_true",
                        help="re-read styles.toml and actors.toml on every call, as production does")
    parser.add_argument("--no-control", action="store_true", help="skip the unchartered control style")
    args = parser.parse_args()

    out_dir = VERIFY_ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="astro-arc-audit-") as tmp:
        install_stubs(Path(tmp), not args.no_cache, not args.no_control)
        install_detectors()
        seed_stage1_cache(CONFIG)

        shipped = [k for k in lp.load_styles() if k != CONTROL_STYLE]
        styles = args.styles or shipped
        registers = lp.load_registers()

        prompts_path = out_dir / "prompts.jsonl"
        with open(prompts_path, "w") as out:
            for style_key in styles:
                run_style(style_key, registers, out)
        shipped_fired = {k: dict(v) for k, v in AUDIT.fired.items()}

        if not args.no_control:
            with open(out_dir / "control.jsonl", "w") as out:
                run_style(CONTROL_STYLE, registers[:2], out)

    digest = hashlib.sha256(prompts_path.read_bytes()).hexdigest()
    (out_dir / "prompts.sha256").write_text(f"{digest}  prompts.jsonl\n")
    runs = sum(1 for _ in open(prompts_path))
    control_only = {k: dict(v) for k, v in AUDIT.fired.items() if k not in shipped_fired}
    summary = {
        "runs": runs,
        "styles": styles,
        "cells": [c for c, _ in cells()],
        "registers": registers,
        "sha256": digest,
        "stage2SystemSha256": hashlib.sha256(AUDIT.stage2_system.encode()).hexdigest(),
        "styleCache": not args.no_cache,
        "chatCalls": dict(AUDIT.chat_calls),
        "firedForShippedStyles": shipped_fired,
        "firedOnlyForControl": control_only,
        "seconds": round(time.monotonic() - started, 1),
    }
    (out_dir / "fallbacks.json").write_text(json.dumps(summary, indent=2, sort_keys=True))

    print(f"{runs} Stage 2 prompts -> {prompts_path}")
    print(f"sha256 {digest}")
    print(f"{summary['seconds']}s, load_styles()/_load_actors() cache {'on' if summary['styleCache'] else 'off'}")
    print("\nFIRED FOR SHIPPED STYLES")
    for key in sorted(shipped_fired):
        per = shipped_fired[key]
        print(f"  {key}\n      {', '.join(f'{s} ({n})' for s, n in sorted(per.items()))}")
    if not args.no_control:
        print("\nFIRED ONLY FOR THE UNCHARTERED CONTROL")
        for key in sorted(control_only):
            print(f"  {key}")


if __name__ == "__main__":
    main()
