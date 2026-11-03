#!/usr/bin/env python3
"""Every style is the user's own file (decision record 2026-11-styles-owned).
The styles that ship with the plugin are seeds: this copies them into the
user's styles folder, and from then on each is a file like any other, to
tweak, hide or delete.

    style_seeds.py            seed; what changed goes to stderr

seed() runs before the panel lists the styles and before every run, and is
safe to run any number of times:

  - A seed with no file in the folder yet is copied in. If it is not one of
    starter-styles.toml's `visible` (and not the selected style) it is
    hidden; after the first seeding it is also marked new.
  - A file that was already there under a seed's key (a user file from
    before seeding) is adopted, never overwritten.
  - A seed changed by a release replaces the user's file only if the file is
    still exactly the seed it was copied from. The charter and the preview
    are tracked apart, so a new preview doesn't stop charter fixes.
  - A seed whose file the user deleted is never copied again, until
    restore_deleted().

The record of what was copied (.seeds.json) sits in the styles folder itself,
so removing the folder starts everything over rather than leaving every
seed marked deleted.
"""

import fcntl
import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import settings  # noqa: E402
from paths import USER_STYLES_DIR  # noqa: E402

BIN_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = BIN_DIR.parent / "pipeline"
SEED_DIR = PIPELINE_DIR / "styles"
STARTER_FILE = PIPELINE_DIR / "starter-styles.toml"
CONFIG_BIN = BIN_DIR / "astro-arc-config"
# What a style is on disk: its charter and its preview.
SUFFIXES = (".toml", ".jpg")
MANIFEST_NAME = ".seeds.json"


class SeedError(Exception):
    pass


def _sha(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _copy(src, dst):
    """src over dst in one step: a reader never sees half a file."""
    tmp = dst.with_name(f".{dst.name}.seed")
    shutil.copyfile(src, tmp)
    tmp.replace(dst)


def starter(path=STARTER_FILE):
    """starter-styles.toml as ({visible keys}, {old key: new key})."""
    with open(path, "rb") as f:
        table = tomllib.load(f)
    return set(table.get("visible", [])), dict(table.get("renamed", {}))


def seeds(seed_dir=SEED_DIR):
    """Every seed key, sorted."""
    return sorted(p.stem for p in Path(seed_dir).glob("*.toml"))


class Seeds:
    """The styles folder and its record of what was seeded into it."""

    def __init__(self, styles_dir=USER_STYLES_DIR, seed_dir=SEED_DIR):
        self.styles_dir = Path(styles_dir)
        self.seed_dir = Path(seed_dir)
        self.path = self.styles_dir / MANIFEST_NAME
        self.fresh = not self.path.exists()
        try:
            data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            data = {}
        seeded = data.get("seeded") if isinstance(data, dict) else None
        # {key: {suffix: the sha256 of the seed last copied in}}
        self.seeded = seeded if isinstance(seeded, dict) else {}
        new = data.get("new") if isinstance(data, dict) else None
        self.new = [k for k in new if isinstance(k, str)] if isinstance(new, list) else []

    def save(self):
        self.styles_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"seeded": self.seeded, "new": self.new}, indent=2, sort_keys=True) + "\n")
        tmp.replace(self.path)

    def target(self, key, suffix=".toml"):
        return self.styles_dir / f"{key}{suffix}"

    def seed_file(self, key, suffix=".toml"):
        return self.seed_dir / f"{key}{suffix}"

    def _deliver(self, key):
        """Copy every file of seed `key` in and record it."""
        entry = {}
        for suffix in SUFFIXES:
            src = self.seed_file(key, suffix)
            if src.is_file():
                _copy(src, self.target(key, suffix))
                entry[suffix] = _sha(src)
        self.seeded[key] = entry

    # ---- what the panel shows --------------------------------------------

    def is_starter(self, key):
        return key in self.seeded and self.seed_file(key).is_file()

    def changed(self, key):
        """Whether the user's files differ from the seed they were copied
        from: a tweak, a new preview, an adopted file of their own."""
        entry = self.seeded.get(key) or {}
        return any(_sha(self.target(key, s)) != h for s, h in entry.items() if self.target(key, s).exists())

    def deleted(self):
        return [k for k in self.seeded if not self.target(k).exists() and self.seed_file(k).is_file()]


@contextmanager
def _locked(styles_dir):
    """One seeding at a time: the panel and a scheduled run can start
    together."""
    styles_dir.mkdir(parents=True, exist_ok=True)
    with open(styles_dir / ".seeds.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _config(*args):
    result = subprocess.run([sys.executable, str(CONFIG_BIN), *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise SeedError(result.stderr.strip() or f"astro-arc-config {args[0]} failed")


def seed(styles_dir=USER_STYLES_DIR, seed_dir=SEED_DIR, starter_file=STARTER_FILE):
    """Bring the styles folder up to date with the seeds (see the module
    doc). Returns what it did: {copied, updated, adopted, hidden, renamed}."""
    styles_dir, seed_dir = Path(styles_dir), Path(seed_dir)
    visible, renamed = starter(starter_file)
    done = {"copied": [], "updated": [], "adopted": [], "hidden": [], "renamed": []}
    with _locked(styles_dir):
        state = Seeds(styles_dir, seed_dir)
        config = settings.read(warn=False)
        for old, new in renamed.items():
            uses = config["artStyle"] == old or old in config["favoriteStyles"] or old in config["hiddenStyles"]
            if uses and not state.target(old).exists():
                _config("--rename-style", old, new)
                done["renamed"].append(f"{old} -> {new}")
        if done["renamed"]:
            config = settings.read(warn=False)
        for key in seeds(seed_dir):
            entry = state.seeded.get(key)
            if entry is None:
                if state.target(key).exists():
                    # A file of the user's under this key: theirs. Recorded
                    # against the seed, so it counts as changed unless it is
                    # the seed byte for byte.
                    state.seeded[key] = {s: _sha(state.seed_file(key, s)) for s in SUFFIXES
                                         if state.seed_file(key, s).is_file()}
                    done["adopted"].append(key)
                    continue
                state._deliver(key)
                done["copied"].append(key)
                if key not in visible and key != config["artStyle"]:
                    done["hidden"].append(key)
                    if not state.fresh:
                        state.new.append(key)
                continue
            if not state.target(key).exists():
                continue  # deleted by the user: stays deleted
            for suffix in SUFFIXES:
                src = state.seed_file(key, suffix)
                if not src.is_file():
                    continue
                theirs, seed_now = _sha(state.target(key, suffix)), _sha(src)
                if seed_now == entry.get(suffix):
                    continue  # this release did not change it
                if theirs == seed_now:
                    entry[suffix] = seed_now  # already the same
                elif theirs == entry.get(suffix):
                    _copy(src, state.target(key, suffix))
                    entry[suffix] = seed_now
                    done["updated"].append(key + suffix)
        if any(done[k] for k in ("copied", "updated", "adopted")) or state.fresh:
            state.save()
        if done["hidden"]:
            _config("--hide-styles", "true", *done["hidden"])
    return done


def revert(key, styles_dir=USER_STYLES_DIR, seed_dir=SEED_DIR):
    """Put the seed's files back over the user's, and back on updates."""
    with _locked(Path(styles_dir)):
        state = Seeds(styles_dir, seed_dir)
        if not state.is_starter(key):
            raise SeedError(f"'{key}' is not a starter style, so there is nothing to revert it to")
        state._deliver(key)
        for suffix in SUFFIXES:
            if not state.seed_file(key, suffix).is_file():
                state.target(key, suffix).unlink(missing_ok=True)
        state.save()


def restore_deleted(styles_dir=USER_STYLES_DIR, seed_dir=SEED_DIR):
    """Copy back every starter style the user deleted. Returns their keys."""
    with _locked(Path(styles_dir)):
        state = Seeds(styles_dir, seed_dir)
        keys = state.deleted()
        for key in keys:
            state._deliver(key)
        if keys:
            state.save()
    return keys


def seen_new(styles_dir=USER_STYLES_DIR, seed_dir=SEED_DIR):
    """The Styles view was opened: nothing is new any more."""
    with _locked(Path(styles_dir)):
        state = Seeds(styles_dir, seed_dir)
        if state.new:
            state.new = []
            state.save()


def seed_or_warn(report=True):
    """seed(), for a caller that carries on either way: a failure is one
    warning on stderr, and with `report` so is what changed."""
    try:
        done = seed()
    except (SeedError, OSError, tomllib.TOMLDecodeError) as e:
        print(f"astro-arc: warning: couldn't set up the starter styles: {e}", file=sys.stderr)
        return False
    for what, keys in done.items():
        if keys and report:
            print(f"astro-arc: styles {what}: {', '.join(keys)}", file=sys.stderr)
    return True


def main():
    return 0 if seed_or_warn() else 1


if __name__ == "__main__":
    sys.exit(main())
