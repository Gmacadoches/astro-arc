"""What the pipeline remembers between runs: the history, and the Stage 1 and
Stage 1.5 caches. One directory, by default paths.PIPELINE_STATE_DIR:

    history.json        the last HISTORY_MAX history entries, newest first
    readings/<key>.json     Stage 1's result per period (the readings cache)
    amplifications/<key>.json   Stage 1.5's meaning per period

Production uses PipelineState(); a test or harness that must leave production
alone passes PipelineState(<a temporary directory>), or no state at all.

A cache entry is served only when it has its content and matches every input
in its cache key: the dict the stage builds from what it was asked (see
llm_pipeline.stage1_cache_key and meaning_cache_key). The key is stored in the
entry, so the rule that writes it and the rule that checks it are one rule,
here, for the pipeline and the cost estimate alike."""

import json
import re
from pathlib import Path

from paths import PIPELINE_STATE_DIR

HISTORY_MAX = 30

# What makes an entry usable, besides matching its key.
READING_CONTENT = ("reading", "distillation")
MEANING_CONTENT = ("constellation",)


class PipelineState:
    def __init__(self, root=PIPELINE_STATE_DIR):
        self.root = Path(root)
        self.history_file = self.root / "history.json"
        self.readings_dir = self.root / "readings"
        self.meanings_dir = self.root / "amplifications"

    # ---- history ------------------------------------------------------------

    def history(self):
        """The saved history entries, newest first; [] when there are none or
        the file is unreadable."""
        try:
            return json.loads(self.history_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []

    def append_history(self, entry):
        """Save `entry` as the newest, keeping HISTORY_MAX. Returns the new list."""
        history = ([entry] + self.history())[:HISTORY_MAX]
        self.root.mkdir(parents=True, exist_ok=True)
        self.history_file.write_text(json.dumps(history, indent=2))
        return history

    # ---- the caches ---------------------------------------------------------

    def cached_reading(self, period_key, key):
        """Stage 1's cached result for this period, or None unless it is
        complete and was written under `key`."""
        return _valid(_read(self.readings_dir / f"{period_key}.json"), READING_CONTENT, key)

    def store_reading(self, period_key, key, result):
        """Cache Stage 1's result with its key. Returns the stored entry."""
        return _write(self.readings_dir / f"{period_key}.json", {**result, **key})

    def cached_meaning(self, period_key, key):
        """Stage 1.5's cached meaning for this period, or None unless it is
        complete and was written under `key`."""
        return _valid(_read(self.meanings_dir / f"{period_key}.json"), MEANING_CONTENT, key)

    def store_meaning(self, period_key, key, result):
        """Cache Stage 1.5's meaning with its key. Returns the stored entry."""
        return _write(self.meanings_dir / f"{period_key}.json", {**result, **key})

    # ---- recency, from the meanings cache ----------------------------------

    def recent_constellations(self, window, exclude=None):
        """The last `window` constellations, most recent first, for Stage 1.5 to
        reach past. `exclude` drops one period key — the one being regenerated,
        which must not count as its own precedent."""
        return self._recent("constellation", window, exclude)

    def recent_actor_kinds(self, window, exclude=None):
        """The last `window` actor kinds, most recent first, for Stage 1.5 to
        pick around. Same source and shape as recent_constellations, because it
        is the same mechanism applied to the other field the model over-uses.

        `none` is skipped: it has its own recency rule (never two days running,
        at most twice in seven) and blocking a kind that is meant to be rare
        would invert that."""
        return self._recent("actorKind", window, exclude, skip=("none",))

    def _recent(self, field, window, exclude, skip=()):
        if not self.meanings_dir.is_dir():
            return []
        out = []
        for path in sorted(self.meanings_dir.glob("*.json"), reverse=True):
            if exclude and path.stem == exclude:
                continue
            value = ((_read(path) or {}).get(field) or "").strip()
            if value and value not in skip and value not in out:
                out.append(value)
            if len(out) >= window:
                break
        return out

    # ---- stored readings, for replays ---------------------------------------

    def stored_reading(self, period_key):
        """The readings cache's entry for this period as it is on disk, whatever
        it was written under; None if there is none."""
        return _read(self.readings_dir / f"{period_key}.json")

    def daily_readings(self):
        """(period key, Stage 1 result) for every complete daily reading in the
        cache, oldest first. For harnesses that replay real days; it does not
        check the cache key, since a replay wants the days that happened."""
        out = []
        for path in sorted(self.readings_dir.glob("*.json")):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem):
                continue
            reading = _read(path)
            if reading and all(reading.get(k) for k in ("reading", "distillation", "narrativePosition")):
                out.append((path.stem, reading))
        return out


def _read(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _write(path, entry):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry, indent=2))
    return entry


def _valid(entry, content, key):
    if (isinstance(entry, dict) and all(entry.get(k) for k in content)
            and all(entry.get(k) == v for k, v in key.items())):
        return entry
    return None
