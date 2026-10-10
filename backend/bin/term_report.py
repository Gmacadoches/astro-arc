#!/usr/bin/env python3
"""Counts the shared repeats in one or more sweeps' prompts, side by side.

Written for STYLE-OVERHAUL.md (Prompt 1): a review of 142 archived runs found
the same verbs, the same far-off distance, the same giant ancient worn
highlight and the same vessels in every style, mostly copied from fixed text
every style receives. This measures each of those per sweep, so a change can
be judged as before/after columns rather than by reading prompts.

Usage:
    term_report.py <sweep> [<sweep> ...]

A <sweep> is a folder name under verification/ (see sweep.py) or a path. Only
the Stage 2 prompt is read (`imagePrompt`), never the style suffix, since the
suffix is identical on every sample and would count as a repeat.

Reports, for each sweep:
  - how many prompts use each verb from any verb list the pipeline offers:
    the global _VERBS_* and _ACTOR_VERBS_* lists, STAGE2_SYSTEM's own actor
    verbs, and every [<style>.verbs] array in styles.toml
  - how many prompts contain each tracked attractor term
  - what the actor is doing: work, or rest, play, ritual or travel. Classified
    by the caption model (gpt-4o-mini unless configured), because no word list
    can tell "carrying a lantern to the shrine" from "carrying sacks". The
    answers are cached in the sweep folder as 00_activity.json, so re-running
    the report costs nothing.
  - STYLE-OVERHAUL's pass conditions, as PASS/FAIL against each sweep.
"""

import json
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm_pipeline as lp  # noqa: E402

VERIFY_ROOT = Path.home() / ".local/state/omarchy/astro-arc/verification"
CONFIG_PATH = Path.home() / ".local/state/omarchy/settings/astro-arc.json"

TERMS = ("far off", "distance", "beyond", "barn", "enormous", "ancient", "older",
         "centuries", "moss", "patched", "mended", "worn", "weathered", "cracked",
         "cistern", "trough", "basin", "bowl", "jar", "pool", "glazed", "porcelain",
         "ceramic")

# How each tracked term is matched. Prefix where the family matters (moss ->
# mossy, barn -> barn-sized), exact-with-plural where a prefix would catch an
# unrelated word (jar -> jarring, pool is fine but bowl -> bowler is not).
_TERM_PATTERNS = {
    "far off": r"\bfar[- ]off\b",
    "distance": r"\bdistances?\b",
    "beyond": r"\bbeyond\b",
    "barn": r"\bbarn\w*",
    "enormous": r"\benormous\w*",
    "ancient": r"\bancient\b",
    "older": r"\bolder\b",
    "centuries": r"\bcentur(y|ies)\b",
    "moss": r"\bmoss\w*",
    "patched": r"\bpatch(ed|es|ing|work)?\b",
    "mended": r"\bmend(ed|s|ing)?\b",
    "worn": r"\bworn\b",
    "weathered": r"\bweather(ed|ing)\b",
    "cracked": r"\bcrack(ed|s|ing)?\b",
    "cistern": r"\bcisterns?\b",
    "trough": r"\btroughs?\b",
    "basin": r"\bbasins?\b",
    "bowl": r"\bbowls?\b",
    "jar": r"\bjars?\b",
    "pool": r"\bpool(s|ed|ing)?\b",
    "glazed": r"\bglaz(e|ed|es|ing)\b",
    "porcelain": r"\bporcelain\b",
    "ceramic": r"\bceramics?\b",
}

# STYLE-OVERHAUL Prompt 1, Step 9.
_GROUPS = {
    "far off / distance / beyond": ("far off", "distance", "beyond"),
    "ancient / older / centuries": ("ancient", "older", "centuries"),
    "worn / weathered / cracked / moss": ("worn", "weathered", "cracked", "moss"),
    "vessels": ("cistern", "trough", "basin", "bowl", "jar", "pool"),
}
_LIMITS = {
    "far off / distance / beyond": 0.50,
    "ancient / older / centuries": 0.30,
    "worn / weathered / cracked / moss": 0.40,
    "vessels": 0.30,
}
VERB_LIMIT = 0.25
WORK_LIMIT = 0.60

# The words that turn a verb phrase into its head verb: "being pinned by",
# "spilling into", "watching something at work".
_SKIP_WORDS = {"being", "a", "an", "the"}
_IRREGULAR = {"fled": "flee", "fleeing": "flee", "bore": "bear", "borne": "bear"}
_PARTICLE_VERBS = {"bear": "down"}
_UNDOUBLED = set()  # bases _base() recovered from a doubled consonant (digging -> dig)  # "bear down on" is one verb; "bear" alone is not


def _head(phrase):
    words = [w for w in re.findall(r"[a-z'-]+", phrase.lower()) if w not in _SKIP_WORDS]
    return words[0] if words else ""


def _base(word):
    """A verb's base form from its -ing or -ed form, good enough to build a
    matching pattern: spilling -> spill, eroding -> erode, digging -> dig,
    carried -> carry, drying -> dry."""
    if word in _IRREGULAR:
        return _IRREGULAR[word]
    for suf in ("ing", "ed"):
        if word.endswith(suf) and len(word) > len(suf) + 2:
            stem = word[: -len(suf)]
            if suf == "ed" and stem.endswith("i"):
                return stem[:-1] + "y"
            if len(stem) > 2 and stem[-1] == stem[-2] and stem[-1] not in "lsz":
                _UNDOUBLED.add(stem[:-1])
                return stem[:-1]
            return stem
    return word


def verb_pattern(base):
    """Regex matching a verb's common inflections from its base. The base may
    have lost a final e to -ing (erod, brac), so both spellings are allowed:
    erod -> erode/erodes/eroded/eroding; dig -> digs/digging; carry -> carried.

    It cannot tell a verb from the same word used otherwise (dry ground, a
    warm wall, the ship's hold), so those counts run slightly high."""
    if base in _PARTICLE_VERBS:
        return rf"\b(bear|bears|bore|bearing|borne)\s+{_PARTICLE_VERBS[base]}\b"
    if base == "flee":
        forms = {"flee", "flees", "fled", "fleeing"}
    elif base.endswith("y") and len(base) > 2 and base[-2] not in "aeiou":
        forms = {base, base[:-1] + "ies", base[:-1] + "ied", base + "ing"}
    elif base in _UNDOUBLED:
        forms = {base, base + "s", base + base[-1] + "ed", base + base[-1] + "ing"}
    else:
        stem = base[:-1] if base.endswith("e") else base
        forms = {base, stem + "e", stem + "es", base + "s", stem + "ed", stem + "ing"}
    return r"\b(" + "|".join(sorted(forms, key=len, reverse=True)) + r")\b"


def _split(listing):
    if isinstance(listing, (list, tuple)):
        return [str(x) for x in listing]
    return [v.strip() for v in str(listing).split(",") if v.strip()]


def verb_sources():
    """{base verb: set of list names it appears in}."""
    sources = {}

    def add(phrases, name):
        for p in phrases:
            head = _head(p)
            if head:
                sources.setdefault(_base(head), set()).add(name)

    for name in ("_VERBS_HARD", "_VERBS_SOFT", "_ACTOR_VERBS_HARD", "_ACTOR_VERBS_SOFT"):
        if hasattr(lp, name):
            add(_split(getattr(lp, name)), name)
    m = re.search(r"Something is being ([^.]+)\.", lp.STAGE2_SYSTEM)
    if m:
        add(_split(m.group(1).replace(" and ", ", ")), "STAGE2_SYSTEM")
    for style, table in lp.load_styles().items():
        verbs = table.get("verbs") if isinstance(table, dict) else None
        if isinstance(verbs, dict):
            for arr, values in verbs.items():
                add(_split(values), f"{style}.{arr}")
    return sources


def load_sweep(arg):
    path = Path(arg)
    if not path.is_dir():
        path = VERIFY_ROOT / arg
    data = json.loads((path / "00_sweep.json").read_text())
    return path, data


ACTIVITY_SYSTEM = """You classify what the main living actor in a described image is doing. Answer with exactly one of:
- work: labour or a chore — hauling, repairing, cleaning, tending, sorting, carrying loads, cooking for sale, operating machinery
- rest: sleeping, sitting idle, eating a meal, waiting, lounging
- play: games, music for pleasure, children playing, leisure, fishing for fun, sketching
- ritual: worship, offering, ceremony, prayer, a festival, a funeral rite
- travel: walking, riding, sailing, arriving, leaving, crossing somewhere on a journey
- none: there is no living actor, or it has no activity at all
Respond with JSON: {"activity": "one of work, rest, play, ritual, travel, none"}"""


def classify_activity(path, samples):
    cache_path = path / "00_activity.json"
    cache = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text())
    config = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    model = config.get("captionModel") or lp.CAPTION_MODEL
    changed = False
    out = []
    for s in samples:
        key = str(s.get("index"))
        if key not in cache or cache[key].get("prompt") != s["imagePrompt"]:
            try:
                result, _ = lp._chat_json(model, ACTIVITY_SYSTEM, s["imagePrompt"], "stage2",
                                          temperature=0.0)
                act = str(result.get("activity") or "none").strip().lower()
            except Exception as exc:  # noqa: BLE001 — a report must survive one bad call
                print(f"term_report: activity call failed: {exc}", file=sys.stderr)
                act = "unknown"
            cache[key] = {"prompt": s["imagePrompt"], "activity": act}
            changed = True
        out.append(cache[key]["activity"])
    if changed:
        cache_path.write_text(json.dumps(cache, indent=2))
    return out


def measure(path, data, sources):
    prompts = [s["imagePrompt"] for s in data["samples"]]
    n = len(prompts)
    low = [p.lower() for p in prompts]
    verbs = {}
    for base in sources:
        pat = re.compile(verb_pattern(base))
        verbs[base] = sum(1 for p in low if pat.search(p))
    terms = {t: sum(1 for p in low if re.search(_TERM_PATTERNS[t], p)) for t in TERMS}
    groups = {g: sum(1 for p in low if any(re.search(_TERM_PATTERNS[t], p) for t in members))
              for g, members in _GROUPS.items()}
    acts = classify_activity(path, data["samples"])
    return {"n": n, "style": data.get("style"), "verbs": verbs, "terms": terms,
            "groups": groups, "activity": acts}


def _pct(k, n):
    return f"{k:>2}/{n} {k / n:>4.0%}" if n else "-"


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    sources = verb_sources()
    runs = []
    for arg in sys.argv[1:]:
        path, data = load_sweep(arg)
        runs.append((path.name, measure(path, data, sources)))

    width = max(12, *(len(name) for name, _ in runs))
    head = f"{'':34}" + "".join(f"{name:>{width + 2}}" for name, _ in runs)

    print("VERBS (prompts using each verb; only verbs used at least once in some sweep)")
    print(head)
    used = sorted((b for b in sources if any(m["verbs"][b] for _, m in runs)),
                  key=lambda b: -max(m["verbs"][b] / m["n"] for _, m in runs))
    for b in used:
        lists = ",".join(sorted(x.replace("_VERBS_", "V_").replace("_ACTOR", "A") for x in sources[b]))
        label = f"{b} [{lists}]"
        if len(label) > 33:
            label = label[:32] + "…"
        print(f"{label:34}" + "".join(f"{_pct(m['verbs'][b], m['n']):>{width + 2}}" for _, m in runs))

    print("\nTERMS (prompts containing each)")
    print(head)
    for t in TERMS:
        print(f"{t:34}" + "".join(f"{_pct(m['terms'][t], m['n']):>{width + 2}}" for _, m in runs))

    print("\nACTOR ACTIVITY")
    print(head)
    for act in ("work", "rest", "play", "ritual", "travel", "none", "unknown"):
        if any(act in m["activity"] for _, m in runs):
            print(f"{act:34}" + "".join(
                f"{_pct(m['activity'].count(act), m['n']):>{width + 2}}" for _, m in runs))

    print("\nPASS CONDITIONS (STYLE-OVERHAUL Prompt 1, Step 9)")
    print(head)

    def row(label, cells):
        print(f"{label:34}" + "".join(f"{c:>{width + 2}}" for c in cells))

    cells = []
    for _, m in runs:
        top = max(m["verbs"].items(), key=lambda kv: kv[1], default=("-", 0))
        share = top[1] / m["n"] if m["n"] else 0
        cells.append(f"{'PASS' if share <= VERB_LIMIT else 'FAIL'} {top[0]} {share:.0%}")
    row(f"no verb > {VERB_LIMIT:.0%}", cells)
    for g, limit in _LIMITS.items():
        row(f"{g} < {limit:.0%}", [
            f"{'PASS' if m['groups'][g] / m['n'] < limit else 'FAIL'} {m['groups'][g] / m['n']:.0%}"
            for _, m in runs])
    row("barn in none", [f"{'PASS' if m['terms']['barn'] == 0 else 'FAIL'} {m['terms']['barn']}"
                         for _, m in runs])
    cells = []
    for _, m in runs:
        actors = [a for a in m["activity"] if a not in ("none", "unknown")]
        share = actors.count("work") / len(actors) if actors else 0
        cells.append(f"{'PASS' if share < WORK_LIMIT else 'FAIL'} {share:.0%} of {len(actors)}")
    row(f"actors working < {WORK_LIMIT:.0%}", cells)


if __name__ == "__main__":
    main()
