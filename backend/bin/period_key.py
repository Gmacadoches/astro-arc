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

    hourly   2026-09-13T14   every hour
    hourly   2026-09-13T12   every 3 hours (interval_hours=3): 12:00-14:59
    daily    2026-09-13
    weekly   2026-W37        ISO week-numbering year + week
    monthly  2026-09
    manual   2026-09-13      same as daily — nothing is scheduled, but the key
                             still names the caches and the output filenames

Hourly takes an interval, one of INTERVAL_HOURS. Each is a divisor of 24, so
the slots line up with the clock (every 3 hours is 00, 03, 06 ...) and the
last slot of the day is as long as the others. A slot is named after the hour
it starts on, so an interval of 1 produces exactly the keys plain hourly
always has, and an existing hourly install sees no change.

Deliberately dependency-free (datetime only) so the shell can call it without
anything installed, and so importing it costs nothing.

Usage:
    period_key.py <frequency> [iso-timestamp]   # the scheduler's key
    period_key.py --interval-hours <N> hourly [ts]   # every N hours
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

# Hours between hourly runs. Divisors of 24 only, see the header.
INTERVAL_HOURS = (1, 2, 3, 4, 6, 8, 12)


def interval_hours(value):
    """`value` as a usable interval, or 1. Same reasoning as an unrecognized
    frequency keying daily: a bad value degrades to plain hourly rather than
    aborting a generation."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 1
    return n if n in INTERVAL_HOURS else 1


def period_key(frequency, when=None, every=1):
    """The key `when` (default: now, local) falls in under `frequency`.
    `every` is the hourly interval and is ignored by the other frequencies.

    An unrecognized frequency keys daily rather than raising: this sits on the
    path that decides whether to spend money, and a typo'd config should
    degrade to the common case, not abort a generation.
    """
    when = when or datetime.now().astimezone()
    if frequency == "hourly":
        every = interval_hours(every)
        return when.strftime("%Y-%m-%dT") + "%02d" % (when.hour // every * every)
    return when.strftime(_FORMATS.get(frequency, _DEFAULT_FORMAT))


def covers(frequency, last_generated_at, now=None, every=1):
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
    return period_key(frequency, last_generated_at, every) == period_key(frequency, now, every)


def cache_key(frequency, when=None):
    """The key the READING and its caches use — which is not always the key the
    scheduler uses.

    "hourly" is about how often a new image appears, not about the sky:
    astro_engine has no hourly arc, so the reading, Stage 1 and Stage 1.5's
    meaning all stay keyed by date however often the schedule fires, while
    astro-arc-generate keys its own output files by the hour. This function is
    where that rule lives; without it the mapping sits implicitly in two files
    that have to agree.
    """
    return period_key("daily" if frequency == "hourly" else frequency, when)


# ---------------------------------------------------------------------------

_CASES = [
    # (iso timestamp, frequency, hourly interval, expected key)
    ("2026-09-13T14:05:00", "hourly", 1, "2026-09-13T14"),
    ("2026-09-13T14:05:00", "daily", 1, "2026-09-13"),
    ("2026-09-13T14:05:00", "weekly", 1, "2026-W37"),
    ("2026-09-13T14:05:00", "monthly", 1, "2026-09"),
    ("2026-09-13T14:05:00", "manual", 1, "2026-09-13"),
    ("2026-09-13T00:00:00", "hourly", 1, "2026-09-13T00"),
    ("2026-09-13T23:59:59", "hourly", 1, "2026-09-13T23"),
    # every N hours: slots start on multiples of N and restart at midnight
    ("2026-09-13T14:05:00", "hourly", 3, "2026-09-13T12"),
    ("2026-09-13T15:00:00", "hourly", 3, "2026-09-13T15"),
    ("2026-09-13T11:59:59", "hourly", 12, "2026-09-13T00"),
    ("2026-09-13T23:59:59", "hourly", 8, "2026-09-13T16"),
    ("2026-09-13T14:05:00", "hourly", 5, "2026-09-13T14"),  # not offered: hourly
    ("2026-09-13T14:05:00", "daily", 6, "2026-09-13"),      # ignored off hourly
    # ISO week edges, where a naive implementation drifts from strftime
    ("2026-01-01T12:00:00", "weekly", 1, "2026-W01"),
    ("2025-12-29T12:00:00", "weekly", 1, "2026-W01"),  # week-year runs ahead
    ("2027-01-03T12:00:00", "weekly", 1, "2026-W53"),  # and behind
    ("2026-12-31T12:00:00", "daily", 1, "2026-12-31"),
]


def _selftest():
    failures = []

    for iso, freq, every, expected in _CASES:
        got = period_key(freq, datetime.fromisoformat(iso), every)
        if got != expected:
            failures.append("python  %s %-8s/%d -> %s, expected %s" % (iso, freq, every, got, expected))
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
            "return M.currentPeriodKey(c[1], new Date(c[0]), c[2]);})));"
        ) % (json.dumps(str(model_js)), json.dumps(_CASES))
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        if out.returncode != 0:
            failures.append("node    could not run Model.js: %s" % out.stderr.strip()[:200])
        else:
            for (iso, freq, every, expected), got in zip(_CASES, json.loads(out.stdout)):
                if got != expected:
                    failures.append("Model.js %s %-8s/%d -> %s, expected %s" % (iso, freq, every, got, expected))
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

    every = 1
    if argv[0] == "--interval-hours":
        if len(argv) < 3:
            print("period_key.py --interval-hours <N> <frequency> [iso-timestamp]", file=sys.stderr)
            return 1
        every = interval_hours(argv[1])
        argv = argv[2:]

    use_cache = argv[0] == "--cache"
    if use_cache:
        argv = argv[1:]
        if not argv:
            print("period_key.py --cache <frequency> [iso-timestamp]", file=sys.stderr)
            return 1

    frequency = argv[0]
    when = datetime.fromisoformat(argv[1]) if len(argv) > 1 else None
    print(cache_key(frequency, when) if use_cache else period_key(frequency, when, every))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
