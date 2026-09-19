#!/usr/bin/env python3
"""The period key: which bucket of time one generation belongs to.

THIS FILE IS THE DEFINITION. Everything that needs a period key gets it from
here — astro_engine.py, model_catalog.py and astro-arc-generate all call in.
There is exactly one other implementation, `currentPeriodKey` in
plugin/Model.js, because QML cannot call Python synchronously inside a
property binding. `--selftest` below checks that the two still agree; run it
after touching either.

It used to live in four places at once (this logic, in bash, in QML/JS, and
twice in Python), kept in sync by hand and by comment. That is what produced
the 2026-09-13 bug where changing the schedule from Daily to Monthly started
a paid image render, because two of those copies disagreed about what
"already generated" meant.

Keys are computed on the LOCAL civil calendar, never by elapsed time. That is
what makes "daily" mean "the date changed" rather than "24 hours passed": 11pm
and 1am the next morning are different keys the moment the clock crosses
midnight, however few hours separate them.

    hourly   2026-09-13T14
    daily    2026-09-13
    weekly   2026-W37        ISO week-numbering year + week
    monthly  2026-09
    manual   2026-09-13      same as daily — nothing is scheduled, but the key
                             still names the caches and the output filenames

Deliberately dependency-free (datetime only) so the shell can call it without
anything installed, and so importing it costs nothing.

Usage:
    period_key.py <frequency> [iso-timestamp]   # the scheduler's key
    period_key.py --cache <frequency> [ts]      # the reading/cache key
    period_key.py --selftest                    # checks every implementation
"""

import sys
from datetime import datetime

FREQUENCIES = ("hourly", "daily", "weekly", "monthly", "manual")

_FORMATS = {
    "hourly": "%Y-%m-%dT%H",
    "weekly": "%G-W%V",
    "monthly": "%Y-%m",
}
_DEFAULT_FORMAT = "%Y-%m-%d"  # daily, manual, and anything unrecognized


def period_key(frequency, when=None):
    """The key `when` (default: now, local) falls in under `frequency`.

    An unrecognized frequency keys daily rather than raising: this sits on the
    path that decides whether to spend money, and a typo'd config should
    degrade to the common case, not abort a generation.
    """
    when = when or datetime.now().astimezone()
    return when.strftime(_FORMATS.get(frequency, _DEFAULT_FORMAT))


def covers(frequency, last_generated_at, now=None):
    """Has a run at `last_generated_at` already covered the period `frequency`
    is in right now?

    Re-keys the run's TIMESTAMP under the current frequency rather than
    trusting whatever key it was filed under — that key was written under the
    frequency in effect at the time, so comparing it directly is what made
    switching Daily -> Monthly read as "September has never been generated"
    and render on the spot.
    """
    if not last_generated_at:
        return False
    return period_key(frequency, last_generated_at) == period_key(frequency, now)


def cache_key(frequency, when=None):
    """The key the READING and its caches use — which is not always the key the
    scheduler uses.

    "hourly" is about how often a new image appears, not about the sky:
    astro_engine has no hourly arc, so the reading, Stage 1, Stage 1.5 and the
    signature all stay keyed by date however often the schedule fires, while
    astro-arc-generate keys its own output files by the hour. This function is
    where that rule lives; without it the mapping sits implicitly in two files
    that have to agree.
    """
    return period_key("daily" if frequency == "hourly" else frequency, when)


# ---------------------------------------------------------------------------

_CASES = [
    # (iso timestamp, frequency, expected key)
    ("2026-09-13T14:05:00", "hourly", "2026-09-13T14"),
    ("2026-09-13T14:05:00", "daily", "2026-09-13"),
    ("2026-09-13T14:05:00", "weekly", "2026-W37"),
    ("2026-09-13T14:05:00", "monthly", "2026-09"),
    ("2026-09-13T14:05:00", "manual", "2026-09-13"),
    ("2026-09-13T00:00:00", "hourly", "2026-09-13T00"),
    ("2026-09-13T23:59:59", "hourly", "2026-09-13T23"),
    # ISO week edges, where a naive implementation drifts from strftime
    ("2026-01-01T12:00:00", "weekly", "2026-W01"),
    ("2025-12-29T12:00:00", "weekly", "2026-W01"),  # week-year runs ahead
    ("2027-01-03T12:00:00", "weekly", "2026-W53"),  # and behind
    ("2026-12-31T12:00:00", "daily", "2026-12-31"),
]


def _selftest():
    failures = []

    for iso, freq, expected in _CASES:
        got = period_key(freq, datetime.fromisoformat(iso))
        if got != expected:
            failures.append("python  %s %-8s -> %s, expected %s" % (iso, freq, got, expected))
    print("python:  %d cases" % len(_CASES))

    # The QML/JS mirror, checked against the same table when node is available.
    import json
    import shutil
    import subprocess
    from pathlib import Path

    model_js = Path(__file__).resolve().parents[2] / "plugin" / "Model.js"
    if not shutil.which("node"):
        print("node:    not installed — skipped (plugin/Model.js NOT verified)")
    elif not model_js.exists():
        print("node:    %s not found — skipped" % model_js)
    else:
        script = (
            "var M=require(%s);"
            "var cases=%s;"
            "console.log(JSON.stringify(cases.map(function(c){"
            "return M.currentPeriodKey(c[1], new Date(c[0]));})));"
        ) % (json.dumps(str(model_js)), json.dumps(_CASES))
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        if out.returncode != 0:
            failures.append("node    could not run Model.js: %s" % out.stderr.strip()[:200])
        else:
            for (iso, freq, expected), got in zip(_CASES, json.loads(out.stdout)):
                if got != expected:
                    failures.append("Model.js %s %-8s -> %s, expected %s" % (iso, freq, got, expected))
            print("node:    %d cases against plugin/Model.js" % len(_CASES))

    for freq, expected in (("hourly", "2026-09-13"), ("daily", "2026-09-13"), ("monthly", "2026-09")):
        got = cache_key(freq, datetime.fromisoformat("2026-09-13T14:05:00"))
        if got != expected:
            failures.append("cache_key %-8s -> %s, expected %s" % (freq, got, expected))
    print("caches:  3 cases (hourly shares daily's reading)")

    if failures:
        print("\nFAIL — implementations disagree:")
        for f in failures:
            print("  " + f)
        return 1
    print("\nOK — every implementation agrees.")
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if argv[0] == "--selftest":
        return _selftest()

    use_cache = argv[0] == "--cache"
    if use_cache:
        argv = argv[1:]
        if not argv:
            print("period_key.py --cache <frequency> [iso-timestamp]", file=sys.stderr)
            return 1

    frequency = argv[0]
    when = datetime.fromisoformat(argv[1]) if len(argv) > 1 else None
    print((cache_key if use_cache else period_key)(frequency, when))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
