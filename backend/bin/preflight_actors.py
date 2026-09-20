#!/usr/bin/env python3
"""Pre-flight for the actor redesign: does the pipeline already produce what
the redesign assumes it produces?

The Actors & Agency brief rests on a claim it calls the discard thesis — that
Stage 1.5's constellation already names a subject of action, and the pipeline
throws that cast away downstream. If that is true, adding an actor is largely a
schema change. If it is false, STAGE15_SYSTEM needs work first and the whole
build order changes.

Two further assumptions need checking before anything is built:

  - `actorKind` arm A (Stage 1.5 picks the actor from the constellation) only
    makes sense if constellations actually vary day to day.
  - `none` (a deliberately empty, purely material image) is meant to be
    constellation-eligible and recency-gated. That only works if eligibility is
    occasional rather than a long unbroken run.

Read-only. Standard library only, like everything else in backend/bin. It
reads the real state directory and writes nothing anywhere, so it is safe to
run against a live install; re-running gives identical output.

    python3 backend/bin/preflight_actors.py

Reads pipeline-meta-*.json in preference to pipeline/amplifications/*.json:
both carry the constellation, but only the meta files also carry `hasPeople`,
the register pair and the final prompt, which is where the actor baseline is.
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import STATE_DIR  # noqa: E402


# ---------------------------------------------------------------------------
# Classification
#
# Two lists, deliberately kept apart. This split is the whole point of the
# discard-thesis question: a constellation naming "the hero at the threshold"
# hands Stage 2 a body it can draw, while one naming "consciousness emerging
# from unity" hands it an abstraction. Both are Neumann's own vocabulary and
# both are legitimate amplification, but only the first is an actor.
#
# Every classification is printed beside its constellation so the numbers can
# be audited by eye rather than trusted.
# ---------------------------------------------------------------------------

EMBODIED = [
    "hero", "mother", "father", "parent", "child", "infant", "twin",
    "dragon", "monster", "serpent", "beast", "captive", "prisoner",
    "traveller", "traveler", "king", "queen", "bride", "groom", "victor",
    "devourer", "the dead", "corpse", "giant", "witness", "guardian",
    "smith", "weaver", "sin-eater", "stranger", "figure", "body",
]

ABSTRACT = [
    "consciousness", "the ego", "ego ", "unity", "wholeness", "centroversion",
    "uroboros", "uroboric", "differentiation", "integration", "psyche",
]

# Constellations that would make a purely material image legitimate.
NONE_ELIGIBLE = [
    "petrif", "tomb", "entomb", "buried", "sealed", "aftermath",
    "abandoned", "deserted", "ruin", "carcass", "remnant", "stillness",
]


def hits(text, needles):
    low = text.lower()
    return sorted({n.strip() for n in needles if n in low})


def archetype_family(text):
    """The dominant named archetype, so near-identical constellations collapse
    together. Ordered most specific first; the first match wins."""
    low = text.lower()
    for key, label in (
        ("world parents", "separation of the World Parents"),
        ("night-sea", "night-sea journey"),
        ("centroversion", "centroversion"),
        ("great mother", "the Great Mother"),
        ("dragon-fight", "dragon-fight"),
        ("hieros gamos", "hieros gamos"),
        ("nigredo", "nigredo"),
        ("albedo", "albedo"),
        ("rubedo", "rubedo"),
        ("uroboros", "uroboros"),
        ("child born", "the child born and held"),
    ):
        if key in low:
            return label
    return "other: " + " ".join(text.split()[:6])


def load_records():
    """(key, constellation, movement, anomaly, hasPeople) newest last.

    Monthly/hourly keys are kept but marked, because a monthly period key is
    not a day and must not count toward the day-to-day variation figures.
    """
    out = []
    for path in sorted(STATE_DIR.glob("pipeline-meta-*.json")):
        key = path.stem.replace("pipeline-meta-", "")
        try:
            d = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        out.append({
            "key": key,
            "daily": bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", key)),
            "constellation": (d.get("constellation") or "").strip(),
            "movement": (d.get("movement") or "").strip(),
            "anomaly": (d.get("anomaly") or d.get("intrusion") or "").strip(),
            "hasPeople": d.get("hasPeople"),
            "register": d.get("register"),
        })
    return out


def rule(title):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def main():
    records = load_records()
    if not records:
        print(f"No pipeline-meta-*.json under {STATE_DIR} — nothing to check.")
        return 1

    daily = [r for r in records if r["daily"] and r["constellation"]]

    rule(f"SOURCE  {STATE_DIR}")
    print(f"pipeline-meta files : {len(records)}")
    print(f"with a constellation: {len([r for r in records if r['constellation']])}")
    print(f"daily keys (used below): {len(daily)}  "
          f"({daily[0]['key']} .. {daily[-1]['key']})" if daily else "")

    # --- 1. the discard thesis --------------------------------------------
    rule("1. DISCARD THESIS — does the constellation already name a subject?")
    embodied_n = abstract_n = 0
    for r in daily:
        e = hits(r["constellation"], EMBODIED)
        a = hits(r["constellation"], ABSTRACT)
        embodied_n += bool(e)
        abstract_n += bool(a)
        mark = "EMBODIED" if e else ("abstract" if a else "NEITHER  ")
        print(f"  {r['key']}  {mark:9}  {r['constellation'][:78]}")
        if e:
            print(f"{'':25}subject: {', '.join(e)}")
    n = len(daily)
    print()
    print(f"  names an EMBODIED subject : {embodied_n}/{n}  ({embodied_n/n:.0%})")
    print(f"  names only an ABSTRACT one: {abstract_n - embodied_n if abstract_n > embodied_n else 0}/{n}")
    print()
    print("  Reading: a high EMBODIED share confirms the thesis — the cast is")
    print("  already generated and discarded downstream, so the actor field is")
    print("  mostly a schema change. A low share means STAGE15_SYSTEM needs work.")

    # --- 2. does it vary? --------------------------------------------------
    rule("2. VARIATION — does the constellation move day to day?")
    fams = [archetype_family(r["constellation"]) for r in daily]
    counts = Counter(fams)
    print("  archetype family                          days   share")
    for fam, c in counts.most_common():
        print(f"  {fam[:40]:40}  {c:4}   {c/n:.0%}")
    consecutive_same = sum(1 for i in range(1, len(fams)) if fams[i] == fams[i - 1])
    exact_dupes = len(daily) - len({r["constellation"] for r in daily})
    movements = Counter(r["movement"] for r in daily if r["movement"])
    print()
    print(f"  distinct families            : {len(counts)} across {n} days")
    print(f"  most common family           : {counts.most_common(1)[0][1]}/{n} "
          f"({counts.most_common(1)[0][1]/n:.0%})")
    print(f"  consecutive days, same family: {consecutive_same}/{max(n-1,1)} "
          f"({consecutive_same/max(n-1,1):.0%})")
    print(f"  byte-identical constellations: {exact_dupes}")
    if movements:
        top_move, top_move_n = movements.most_common(1)[0]
        print(f"  most repeated `movement`     : {top_move_n}/{n}  \"{top_move[:52]}\"")
    print()
    print("  Compare against the texture benchmark the project already holds:")
    print("  consecutive days land in the same dial cell 8% of the time.")

    # --- 3. none eligibility ----------------------------------------------
    rule("3. `none` ELIGIBILITY — and how long the runs get")
    flags = []
    for r in daily:
        h = hits(r["constellation"] + " " + r["movement"], NONE_ELIGIBLE)
        flags.append(bool(h))
        if h:
            print(f"  {r['key']}  eligible via: {', '.join(h)}")
    eligible = sum(flags)
    longest = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        longest = max(longest, cur)
    print()
    print(f"  eligible days        : {eligible}/{n}  ({eligible/n:.0%})")
    print(f"  longest eligible run : {longest}")
    print()
    print("  Reading: the design caps `none` at never-two-running, max twice in")
    print("  seven. A long run here means the recency veto, not the")
    print("  constellation, would be doing the real work.")

    # --- 4. actor baseline -------------------------------------------------
    rule("4. ACTOR BASELINE — how many images contain a person today")
    with_flag = [r for r in records if r["hasPeople"] is not None]
    people = [r for r in with_flag if r["hasPeople"]]
    if with_flag:
        print(f"  records carrying hasPeople: {len(with_flag)}")
        print(f"  hasPeople = true          : {len(people)}/{len(with_flag)} "
              f"({len(people)/len(with_flag):.0%})")
        for r in people:
            print(f"      {r['key']}  register={r['register']}")
    else:
        print("  No hasPeople recorded in any meta file.")
    print()
    print("  Target after the redesign: 70-80% of images with a locus of")
    print("  experience (which is broader than hasPeople — it counts animals,")
    print("  machines and the dead too).")

    # --- 5. anomaly repetition (not asked for, but free and relevant) ------
    rule("5. ANOMALY REPETITION — bonus check")
    mats = Counter()
    for r in records:
        for word in ("neolithic", "paleolithic", "cycladic", "etruscan",
                     "bronze", "marble", "stone", "clay", "iron", "wood",
                     "bone", "wax", "lead", "glass"):
            if word in r["anomaly"].lower():
                mats[word] += 1
    anomalies = [r for r in records if r["anomaly"]]
    if anomalies:
        print(f"  anomalies recorded: {len(anomalies)}")
        print("  material / era word            count   share")
        for w, c in mats.most_common(8):
            print(f"  {w:28}  {c:4}   {c/len(anomalies):.0%}")
        print()
        print("  Reading: the anomaly is typed as out-of-scale or out-of-TIME,")
        print("  and out-of-time is defined as OLDER-only. If one era dominates,")
        print("  the anomaly has collapsed into a single recurring prop — which")
        print("  is the invariant-shock problem the 2026-09-12 rewrite was for.")

    rule("VERDICT")
    thesis = embodied_n / n if n else 0
    varies = len(counts) > 1 and consecutive_same / max(n - 1, 1) < 0.5
    print(f"  Discard thesis       : {'CONFIRMED' if thesis >= 0.6 else 'NOT CONFIRMED'} "
          f"({thesis:.0%} embodied)")
    print(f"  Constellations vary  : {'YES' if varies else 'NO'}")
    print(f"  `none` run length OK : {'YES' if longest <= 2 else 'NO'} (longest {longest})")
    print()
    if not varies:
        print("  STOP CONDITION MET. The build plan says: if constellations do not")
        print("  vary daily, the actor design assumes something false. Arm A of the")
        print("  actorKind test cannot work, and a chart-derived actor will repeat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
