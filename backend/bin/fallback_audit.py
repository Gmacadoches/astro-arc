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
  control.jsonl   a synthetic style with no charter at all. Before
                  charter-required it proved each detector could fire; now it
                  proves build() refuses an unchartered style. Not part of
                  the hash.

build() is given a PipelineState in a temporary directory (history, the
Stage 1 and 1.5 caches), so the real state is neither read nor written.

Every build() is handed the same Catalog by default, so the theme files are
read once for the whole audit. --no-cache hands each build() a fresh one, as
production reads them once per run.

    fallback_audit.py --out baseline

The hash a change must leave alone is pinned in backend/tests/prompts.sha256,
and backend/tests/test_prompts.py fails when the audit no longer gives it. A
change to the prompts you mean to make re-pins it in the same commit:

    fallback_audit.py --out <name> --pin
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
from paths import VERIFY_DIR  # noqa: E402
from pipeline_state import PipelineState  # noqa: E402


PERIOD_KEY = "audit-2026-10-16"
CONFIG = {"stage1Model": "stub-model", "stage2Model": "stub-model", "captionModel": "stub-model"}
POLARITIES = ("hard", "soft", "none")
BANDS = ("lo", "mid", "hi")
CONTROL_STYLE = "__unchartered__"
PINNED = Path(__file__).resolve().parents[1] / "tests" / "prompts.sha256"

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
        self.chat = None  # the stub chat client, set by install_stubs
        self.state = None  # the temporary PipelineState, set by install_stubs
        self.catalog = None  # the Catalog, set by install_stubs
        self.catalog_for_run = None  # what each build() is handed, set by install_stubs

    def hit(self, symbol, how):
        self.fired[f"{symbol}: {how}"][self.style] += 1


AUDIT = Audit()


def _wrap(name, after):
    """Replace lp.<name> with a wrapper that calls the original, then
    after(result, *args, **kwargs). Module globals are looked up at call time,
    so every caller inside llm_pipeline goes through the wrapper. A symbol the
    pipeline no longer has (charter-required removed most of them) is skipped:
    its detector has nothing left to watch."""
    if not hasattr(lp, name):
        return
    original = getattr(lp, name)

    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        after(result, *args, **kwargs)
        return result

    setattr(lp, name, wrapper)


def _style(style_key):
    return AUDIT.catalog.styles.get(style_key) or {}


def install_detectors():
    # The loader detectors (load_style_brief, _default_signature,
    # load_style_highlight, _global_verbs, load_style_conditions,
    # load_style_world, load_style_suffix) went with the functions they
    # watched: charter-required removed their fallbacks, and the catalog
    # (2026-10-24) the functions themselves.

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
        if hasattr(lp, "_VERBS_HARD"):
            AUDIT.hit("_VERBS_*/_ACTOR_VERBS_*", "dial_from_texture seeds dial verbs from the globals")
        if hasattr(lp, "_LIGHT_BY_EXPOSURE"):
            AUDIT.hit("_LIGHT_BY_EXPOSURE", "dial_from_texture seeds dial light from the global table")
    _wrap("dial_from_texture", after_dial)

    original_draw = lp.draw_style_choices

    def draw(style, dial, history, period_key):
        if not dial.get("polarity"):
            AUDIT.hit("draw_style_choices", 'dial has no polarity, `or "soft"` used')
        return original_draw(style, dial, history, period_key)
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
        # Since the catalog (2026-10-24) the brief and highlight come from the
        # style Stage 2 is handed; before it they were keywords of their own.
        style = kwargs.get("style") or (args[4] if len(args) > 4 else None)
        brief = style.brief_rules if style is not None else kwargs.get("style_brief")
        highlight = style.highlight if style is not None else kwargs.get("highlight")
        if brief is None:
            AUDIT.hit("DEFAULT_STAGE2_BRIEF", "stage2_image_prompt, style_brief is None")
        if not highlight:
            AUDIT.hit("DEFAULT_HIGHLIGHT", "stage2_image_prompt, `highlight or DEFAULT_HIGHLIGHT`")
        d = dial or getattr(lp, "NO_TEXTURE_DIAL", {})
        if not d.get("prominence"):
            AUDIT.hit("stage2_image_prompt", "`prominence or _PROMINENCE_BY_INTENSITY['mid']`")
        if hasattr(lp, "_VERBS_HARD"):
            if d.get("verbs") in (lp._VERBS_HARD, lp._VERBS_SOFT):
                AUDIT.hit("_VERBS_*/_ACTOR_VERBS_*", "global process verbs reached Stage 2 verbatim")
            if d.get("actorVerbs") in (*lp._ACTOR_VERBS_HARD.values(), *lp._ACTOR_VERBS_SOFT.values()):
                AUDIT.hit("_VERBS_*/_ACTOR_VERBS_*", "global actor verbs reached Stage 2 verbatim")
        no_texture = getattr(lp, "NO_TEXTURE_DIAL", {})
        if "verbs" in no_texture and d.get("verbs") == no_texture["verbs"]:
            AUDIT.hit("NO_TEXTURE_DIAL", "its fixed verbs reached Stage 2")
        if d.get("light") and d.get("light") in getattr(lp, "_LIGHT_BY_EXPOSURE", {}).values():
            AUDIT.hit("_LIGHT_BY_EXPOSURE", "global light line reached Stage 2 (style has no [light] line for the band)")
        if not d.get("light"):
            AUDIT.hit("stage2_image_prompt", "no light line at all")
        if d.get("condition") in lp.CONDITIONS and not _style(AUDIT.style).get("conditions"):
            AUDIT.hit("CONDITIONS", "global wear scale reached Stage 2")
        return original_stage2(*args, **kwargs)
    lp.stage2_image_prompt = stage2


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
    # Passed to build() as its chat client: the stub adapter at the chat seam.
    AUDIT.chat = chat

    # The safety net: if a call ever bypassed the client, it fails loudly here
    # rather than reaching the network or the keyring.
    def no_network(*_a, **_k):
        raise RuntimeError("fallback_audit: network access attempted")
    lp.openai_chat = no_network
    lp.urllib.request.urlopen = no_network
    lp.open_no_redirect = no_network
    lp.get_api_key = no_network

    # Passed to build() as its state: an empty history, and caches nobody
    # else reads.
    AUDIT.state = PipelineState(state_dir)

    # The registers are the variable under test, so build() is handed them.
    lp.pick_registers = lambda history, registers, cohesion="collide": AUDIT.registers

    # The catalog build() is handed: one for every run, or with --no-cache a
    # fresh one per run as production reads it. The control style is a draft
    # theme file with a label, a suffix and a preview and nothing else, so the
    # only thing it can show is the charter refusal.
    drafts = []
    if control_style:
        control = state_dir / f"{CONTROL_STYLE}.toml"
        control.write_text('label = "Unchartered Control"\nsuffix = "control suffix"\n')
        control.with_suffix(".jpg").write_bytes(b"")
        drafts.append(control)
    AUDIT.catalog = lp.Catalog.load(drafts=drafts)
    AUDIT.catalog_for_run = ((lambda: AUDIT.catalog) if cache_styles
                             else (lambda: lp.Catalog.load(drafts=drafts)))


def seed_stage1_cache(config):
    AUDIT.state.store_reading(PERIOD_KEY, lp.stage1_cache_key(config), STUB_RESPONSES["stage1"])


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
            for path in AUDIT.state.meanings_dir.glob("*.json"):
                path.unlink()
            AUDIT.registers = (primary, secondary)
            AUDIT.captured = []
            reading = {"arc": {"periodKey": PERIOD_KEY, "frequency": "daily", "texture": texture}}
            if texture is None and not hasattr(lp, "NO_TEXTURE_DIAL"):
                # Since 2026-10-16 a reading with no texture is broken, and
                # build() must refuse it. Recorded in fallbacks.json, not in
                # prompts.jsonl, which holds only prompts that were sent.
                try:
                    lp.build(reading, config, chat=AUDIT.chat, state=AUDIT.state, catalog=AUDIT.catalog_for_run())
                except lp.PipelineError:
                    AUDIT.hit("dial_from_texture", "refused a reading with no texture")
                    continue
                raise SystemExit(f"{style_key}: a reading with no texture was not refused")
            if style_key == CONTROL_STYLE:
                # Since charter-required, an unchartered style must be refused
                # before anything is billed. That refusal is the control.
                try:
                    lp.build(reading, config, chat=AUDIT.chat, state=AUDIT.state, catalog=AUDIT.catalog_for_run())
                except lp.PipelineError as exc:
                    AUDIT.hit("require_charter", "refused the unchartered control")
                    out.write(json.dumps({"style": style_key, "cell": cell_name, "primary": primary,
                                          "secondary": secondary, "refused": str(exc)},
                                         sort_keys=True, ensure_ascii=False) + "\n")
                    continue
                raise SystemExit("the unchartered control was not refused")
            lp.build(reading, config, chat=AUDIT.chat, state=AUDIT.state, catalog=AUDIT.catalog_for_run())
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
                        help="load a fresh catalog for every build(), as production does once per run")
    parser.add_argument("--no-control", action="store_true", help="skip the unchartered control style")
    parser.add_argument("--pin", action="store_true",
                        help=f"write the hash to {PINNED.relative_to(PINNED.parents[2])} (a full run only)")
    args = parser.parse_args()
    if args.pin and args.styles:
        parser.error("--pin needs every shipped style: drop --styles")

    out_dir = VERIFY_DIR / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="astro-arc-audit-") as tmp:
        install_stubs(Path(tmp), not args.no_cache, not args.no_control)
        install_detectors()
        seed_stage1_cache(CONFIG)

        # Shipped themes only: a user's own themes would change the baseline.
        shipped = [k for k, t in AUDIT.catalog.styles.items() if getattr(t, "source", None) == "shipped"]
        styles = args.styles or shipped
        registers = AUDIT.catalog.registers

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
        "charterWarnings": {k: AUDIT.catalog.validate(k)[1] for k in sorted(styles)},
        "seconds": round(time.monotonic() - started, 1),
    }
    (out_dir / "fallbacks.json").write_text(json.dumps(summary, indent=2, sort_keys=True))

    print(f"{runs} Stage 2 prompts -> {prompts_path}")
    print(f"sha256 {digest}")
    if args.pin:
        PINNED.write_text(f"{digest}\n")
        print(f"pinned in {PINNED}")
    print(f"{summary['seconds']}s, catalog cache {'on' if summary['styleCache'] else 'off'}")
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
