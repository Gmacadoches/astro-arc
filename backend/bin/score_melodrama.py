#!/usr/bin/env python3
"""Score llm_pipeline.caption_check against the hand-labelled calibration set.

The melodrama rule is a judgement call, so the only honest way to change the
check is to change it and re-score. This prints the confusion in full — which
scene, what the judge said, which signal fired — because an aggregate number
hides the thing you need to see, namely WHICH kind of scene it gets wrong.

`unsure` rows are reported but excluded from the score: they are the rows
where the line genuinely is not decided, and scoring against them would be
scoring against a coin toss.

Costs about a cent (one cheap judge call per scene). Read-only apart from the
optional --write, which records the new machine verdicts back into the TOML so
the file keeps showing the current state.

    python3 backend/bin/score_melodrama.py
    python3 backend/bin/score_melodrama.py --model gpt-4o-mini --write
"""

import argparse
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm_pipeline as lp  # noqa: E402

CALIBRATION = Path(__file__).resolve().parents[1] / "pipeline" / "melodrama-calibration.toml"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--write", action="store_true",
                    help="record the new verdicts into the TOML's `machine` fields")
    args = ap.parse_args()

    scenes = tomllib.loads(CALIBRATION.read_text())["scene"]
    rows, scored, correct = [], 0, 0
    fp, fn = [], []

    print(f"{'id':22} {'label':7} {'check':6} {'B':2} caption / signals")
    print("-" * 104)
    for s in scenes:
        caption, hits, ok = lp.caption_check(s["text"], args.model)
        verdict = "pass" if ok else ("fail" if ok is False else "skip")
        rows.append((s, verdict))
        label = s.get("label") or ""
        mark = ""
        if label in ("pass", "fail"):
            scored += 1
            if verdict == label:
                correct += 1
            elif label == "fail":
                mark, _ = "  <-- MISS (melodrama got through)", fn.append(s["id"])
            else:
                mark, _ = "  <-- FALSE ALARM (allowed scene blocked)", fp.append(s["id"])
        elif label == "unsure":
            mark = "  (unsure, not scored)"
        flag = "B" if s.get("borderline") else ""
        print(f"{s['id']:22} {label or '-':7} {verdict:6} {flag:2} {caption!r} {','.join(hits)}{mark}")

    print()
    print(f"agreement: {correct}/{scored} scored rows "
          f"({correct / scored:.0%})" if scored else "nothing labelled")
    print(f"  misses      (melodrama passed): {fn or 'none'}")
    print(f"  false alarms (allowed blocked): {fp or 'none'}")
    print()
    print("  A miss lets one bad image through. A false alarm costs a resample and,")
    print("  if it is systematic, quietly narrows what the pipeline can ever show —")
    print("  which is the failure the old headcount rule already made once.")

    if args.write:
        text = CALIBRATION.read_text()
        for s, verdict in rows:
            if verdict == "skip":
                continue
            i = text.index(f'id = "{s["id"]}"')
            j = text.index("machine = ", i)
            end = text.index("\n", j)
            text = text[:j] + f'machine = "{verdict}"' + text[end:]
        CALIBRATION.write_text(text)
        print(f"\nwrote verdicts back into {CALIBRATION.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
