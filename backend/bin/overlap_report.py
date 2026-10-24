#!/usr/bin/env python3
"""How much two styles' prompts share, for the same reading and register.

Written for STYLE-OVERHAUL Prompt 5: every style is swept on the same
readings and registers, and a pair whose prompts share most of their
concrete nouns is two labels on one world.

Usage:
    overlap_report.py <prefix>  STYLE [STYLE ...]

Reads verification/<prefix>-<style>/00_sweep.json for each style (from
sweep.py). Samples are matched across styles by their primary register.
For each prompt the concrete nouns are listed by the caption model (cached as
00_nouns.json beside the sweep; lemmatised to singular, lower case), and
each pair of styles is scored as the mean, over matched registers, of
|shared nouns| / |nouns in either| (Jaccard). Pairs above 30% are flagged
with the nouns they share most.
"""

import json
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm_pipeline as lp  # noqa: E402
from paths import VERIFY_DIR  # noqa: E402
import settings  # noqa: E402

FLAG = 0.30

NOUN_SYSTEM = """List the concrete nouns in a described image: the physical things, materials, creatures, people and places named. Singular, lower case, one or two words each, no adjectives, no abstract nouns (no light, shadow, scene, frame, distance, edge, side). Respond with JSON: {"nouns": ["..."]}"""


def nouns_for(path, data, model, chat):
    cache_path = path / "00_nouns.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    changed = False
    for s in data["samples"]:
        key = str(s["index"])
        if cache.get(key, {}).get("prompt") != s["imagePrompt"]:
            try:
                result, _ = chat(model, NOUN_SYSTEM, s["imagePrompt"], "stage2", temperature=0.0)
                found = sorted({str(n).strip().lower() for n in result.get("nouns") or [] if str(n).strip()})
            except Exception as exc:  # noqa: BLE001
                print(f"overlap_report: noun call failed: {exc}", file=sys.stderr)
                found = []
            cache[key] = {"prompt": s["imagePrompt"], "nouns": found}
            changed = True
    if changed:
        cache_path.write_text(json.dumps(cache, indent=2))
    return {s["primaryRegister"]: set(cache[str(s["index"])]["nouns"]) for s in data["samples"]}


def main():
    if len(sys.argv) < 4:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    prefix, styles = sys.argv[1], sys.argv[2:]
    config = settings.load()
    model = config.get("captionModel") or lp.caption_model_for(lp.provider_of(config))
    chat = lp.chat_client(config)
    by_style = {}
    for st in styles:
        path = VERIFY_DIR / f"{prefix}-{st}"
        by_style[st] = nouns_for(path, json.loads((path / "00_sweep.json").read_text()), model, chat)

    scores, shared = {}, {}
    for a, b in combinations(styles, 2):
        regs = sorted(set(by_style[a]) & set(by_style[b]))
        vals, common = [], Counter()
        for r in regs:
            na, nb = by_style[a][r], by_style[b][r]
            if na | nb:
                vals.append(len(na & nb) / len(na | nb))
            common.update(na & nb)
        scores[(a, b)] = scores[(b, a)] = sum(vals) / len(vals) if vals else 0.0
        shared[(a, b)] = common

    w = max(len(s) for s in styles) + 2
    print(f"NOUN OVERLAP ({prefix}), mean Jaccard over matched registers; * = above {FLAG:.0%}")
    print(" " * w + "".join(f"{s[:10]:>12}" for s in styles))
    for a in styles:
        cells = []
        for b in styles:
            if a == b:
                cells.append(f"{'—':>12}")
            else:
                v = scores[(a, b)]
                cells.append(f"{v:>11.0%}{'*' if v > FLAG else ' '}")
        print(f"{a:<{w}}" + "".join(cells))
    flagged = [(a, b) for a, b in combinations(styles, 2) if scores[(a, b)] > FLAG]
    for a, b in flagged:
        top = ", ".join(f"{n} ({c})" for n, c in shared[(a, b)].most_common(10))
        print(f"\n{a} / {b}: {scores[(a, b)]:.0%} — shared: {top}")
    if not flagged:
        print("\nNo pair above the flag.")


if __name__ == "__main__":
    main()
