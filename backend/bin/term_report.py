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
  - how many prompts use each verb phrase from any verb list the pipeline offers
    (inflected forms only, with the phrase's particle or object word nearby):
    STAGE2_SYSTEM's own actor verbs and every theme's [verbs] array in
    the theme files (the global verb lists went 2026-10-16)
  - how many prompts contain each tracked attractor term
  - what the actor is doing: work, or rest, play, ritual or travel. Classified
    by the configured Stage 2 model, because no word list
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
from paths import CONFIG_FILE, VERIFY_DIR  # noqa: E402


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
_UNDOUBLED = set()  # bases _base() recovered from a doubled consonant (digging -> dig)


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


_PREPOSITIONS = {"into", "on", "against", "through", "at", "under", "with", "beside", "to",
                 "by", "across", "over", "past", "for", "from", "in", "of", "something", "outside"}


def _forms(base):
    """A verb's inflected forms from its base, never the bare base itself: the
    bare form is usually also a noun or adjective (fish, dry, black, lean), and
    counting it would score "a dry wall" as the verb. erod -> eroding, eroded,
    erodes; dig -> digging, digs; carry -> carried."""
    if base == "flee":
        return {"flees", "fled", "fleeing"}
    if base.endswith("y") and len(base) > 2 and base[-2] not in "aeiou":
        return {base[:-1] + "ies", base[:-1] + "ied", base + "ing"}
    if base in _UNDOUBLED:
        return {base + "s", base + base[-1] + "ed", base + base[-1] + "ing"}
    stem = base[:-1] if base.endswith("e") else base
    return {stem + "ing", stem + "ed", stem + "es", stem + "s"}


def phrase_pattern(phrase):
    """Regex for one verb phrase as the lists write it: the head verb in an
    inflected form, then the phrase's next word within three words when it has
    one ("blacking out", "hanging laundry", "leaning against", "sitting
    with"). -ed forms count only for a passive phrase ("being mended"): for an
    -ing phrase they are nearly always adjectives ("stacked towers", "a
    cracked dial"), and so is a bare head ("a brazier sits under the rail" is
    placement, not "sitting with")."""
    raw = re.findall(r"[a-z'-]+", phrase.lower())
    passive = bool(raw) and raw[0] == "being"
    words = [w for w in raw if w not in _SKIP_WORDS]
    if not words:
        return None
    base = _base(words[0])
    rest = [w for w in words[1:] if w not in ("a", "an", "the", "something")]
    if base == "bear" and rest[:1] == ["down"]:
        return r"\b(bears|bore|bearing|borne)\s+down\b"
    forms = _forms(base)
    if not passive:
        forms = {f for f in forms if not f.endswith("ed") or f == "fled"}
    # (?<!-): a hyphenated compound ("herb-drying rack") is a noun, not the verb.
    head = r"(?<![-\w])(" + "|".join(sorted(forms, key=len, reverse=True)) + r")\b"
    if rest:
        tail = re.escape(rest[0] if rest[0] not in _PREPOSITIONS or len(rest) == 1 else rest[1])
        return head + r"(?:\W+\w+){0,3}?\W+" + tail + r"\w*"
    return head


def _split(listing):
    if isinstance(listing, dict):
        return [v for group in listing.values() for v in _split(group)]
    if isinstance(listing, (list, tuple)):
        return [str(x) for x in listing]
    return [v.strip() for v in str(listing).split(",") if v.strip()]


def verb_sources():
    """{base verb: set of list names it appears in}."""
    sources = {}

    def add(phrases, name):
        for p in phrases:
            p = p.strip().lower()
            if p and phrase_pattern(p):
                sources.setdefault(p, set()).add(name)

    m = re.search(r"Something is being ([^.]+)\.", lp.STAGE2_SYSTEM)
    if m:
        add(_split(m.group(1).replace(" and ", ", ")), "STAGE2_SYSTEM")
    for style, table in lp.Catalog.load().styles.items():
        verbs = table.get("verbs") if isinstance(table, dict) else None
        if isinstance(verbs, dict):
            for arr, values in verbs.items():
                add(_split(values), f"{style}.{arr}")
    return sources


def load_sweep(arg):
    path = Path(arg)
    if not path.is_dir():
        path = VERIFY_DIR / arg
    data = json.loads((path / "00_sweep.json").read_text())
    return path, data


ACTIVITY_SYSTEM = """You classify what the actor in a described image is doing IN THIS MOMENT. The actor is the person, creature, spirit or machine-figure the description shows doing something; never a plant, a building or the place, however much of the frame they fill. Judge by the verb the description gives the actor, never by their trade, their workplace or the objects around them: a mechanic eating lunch is resting, a nurse riding pillion is travelling, a clerk pasting a prayer sticker or ringing a bell and scattering salt is at ritual, a nurse racing pill bottles along a bench is playing. Where the actor does two things, judge the one the description leads with. Answer with exactly one of:
- work: labour or a chore — hauling, repairing, cleaning, tending, sorting, carrying loads, cooking for sale, operating machinery
- rest: sleeping, sitting idle, eating a meal, waiting, lounging
- play: games, gambling, racing for fun, music for pleasure, children playing, leisure, fishing for fun, sketching
- ritual: worship, offering, ceremony, prayer, a festival, a funeral rite
- travel: walking, riding, sailing, arriving, leaving, crossing somewhere on a journey
- none: there is no actor at all
Respond with JSON: {"activity": "one of work, rest, play, ritual, travel, none"}"""


# Bumped when ACTIVITY_SYSTEM changes, so cached answers are re-asked.
ACTIVITY_VERSION = 5


def classify_activity(path, samples):
    cache_path = path / "00_activity.json"
    cache = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text())
    config = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    # The writing model, not the caption judge: gpt-4o-mini returned "none" for
    # 5 of 12 prompts that plainly had a nurse in them (2026-10-10).
    model = config.get("stage2Model") or config.get("captionModel") or lp.caption_model_for(lp.provider_of(config))
    chat = lp.chat_client(config)
    changed = False
    out = []
    for s in samples:
        key = str(s.get("index"))
        if (key not in cache or cache[key].get("prompt") != s["imagePrompt"]
                or cache[key].get("v") != ACTIVITY_VERSION):
            try:
                result, _ = chat(model, ACTIVITY_SYSTEM, s["imagePrompt"], "stage2", temperature=0.0)
                act = str(result.get("activity") or "none").strip().lower()
            except Exception as exc:  # noqa: BLE001 — a report must survive one bad call
                print(f"term_report: activity call failed: {exc}", file=sys.stderr)
                act = "unknown"
            cache[key] = {"prompt": s["imagePrompt"], "activity": act, "v": ACTIVITY_VERSION}
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
    for phrase in sources:
        pat = re.compile(phrase_pattern(phrase))
        verbs[phrase] = sum(1 for p in low if pat.search(p))
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
        lists = ",".join(sorted(sources[b]))
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
