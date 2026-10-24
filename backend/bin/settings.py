"""The settings, read one way for everyone: backend/pipeline/settings.toml
says what each key is, and this module applies it.

    read()   the config as stored: every key the schema names, defaulted, a
             value that does not fit its type replaced by its default (with
             a warning), and keys the schema does not name passed through.
             astro-arc-config prints this and the panel shows it.
    load()   what a run uses: read(), with "" resolved where the schema says
             what it means (openaiModel "" is the provider's image model) and
             the ASTRO_ARC_PROVIDER / ASTRO_ARC_REASONING harness overrides
             applied. The pipeline and the harnesses call this.

astro-arc-config is the only writer of the settings file. It prints through
dumps(), which formats exactly as jq did when it was a shell script, numbers
kept as written, so a file it rewrites changes only where a value did.
"""

import json
import os
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import CONFIG_FILE  # noqa: E402

SCHEMA_FILE = Path(__file__).resolve().parents[1] / "pipeline" / "settings.toml"
SIZE_PATTERN = re.compile(r"^[0-9]{2,5}x[0-9]{2,5}$")

_schema = None


def schema():
    """settings.toml, read once per process."""
    global _schema
    if _schema is None:
        with open(SCHEMA_FILE, "rb") as f:
            _schema = tomllib.load(f)
    return _schema


class Number:
    """A JSON number as written in the file ("0.50", "40.0"), so a value
    nobody changed is printed back exactly as it was read."""

    def __init__(self, text):
        self.text = text

    @property
    def value(self):
        return float(self.text) if any(c in self.text for c in ".eE") else int(self.text)

    def __eq__(self, other):
        return isinstance(other, Number) and other.text == self.text

    def __repr__(self):
        return f"Number({self.text!r})"


def _num(value):
    """A number's value, whether parsed as a Number or plainly; None if it is
    not a number (bool included)."""
    if isinstance(value, Number):
        return value.value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def _provider_value(name, provider):
    table = schema()["providers"]
    return table.get(provider, table["openai"])[name]


def default(key, provider="openai"):
    spec = schema()["keys"][key]
    if spec["type"] == "coordinate":
        return None
    value = spec["default"]
    if isinstance(value, str) and value.startswith("provider."):
        return _provider_value(value.split(".", 1)[1], provider)
    return value


def defaults(provider="openai"):
    """Every key the schema names, at its default, in schema order."""
    return {key: default(key, provider) for key in schema()["keys"]}


def fits(key, value):
    """Whether `value` is something `key` may hold."""
    spec = schema()["keys"][key]
    kind = spec["type"]
    if kind == "string":
        return isinstance(value, str)
    if kind == "nonempty":
        return isinstance(value, str) and value != ""
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "enum":
        if isinstance(value, (str, bool)) or value is None:
            return value in spec["values"] and not isinstance(value, bool)
        n = _num(value)
        return n is not None and n in spec["values"] and n == int(n)
    if kind == "int":
        n = _num(value)
        return n is not None and n == int(n) and spec["min"] <= n <= spec["max"]
    if kind == "number":
        n = _num(value)
        return n is not None and n >= spec["min"]
    if kind == "coordinate":
        return value is None or _num(value) is not None
    if kind == "size":
        return isinstance(value, str) and (value == "auto" or bool(SIZE_PATTERN.match(value)))
    raise ValueError(f"settings.toml: {key} has unknown type {kind!r}")


def _merge(base, over):
    """jq's `base * over`: objects merged recursively, `over` winning, keys
    in `base` keeping their place and new ones appended."""
    out = dict(base)
    for key, value in over.items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _show(value):
    return dumps(value, indent=None) if not isinstance(value, str) else repr(value)


def clean(data, warn=True):
    """`data` (a parsed settings file) over the defaults, with every value
    that does not fit its key replaced by its default. The provider is
    settled first, since the model defaults depend on it."""
    provider = data.get("provider") if fits("provider", data.get("provider")) else "openai"
    merged = _merge(defaults(provider), data)
    for key in schema()["keys"]:
        if not fits(key, merged[key]):
            fallback = default(key, provider)
            if warn:
                print(f"astro-arc: warning: settings: {key} {_show(merged[key])} is not valid; "
                      f"using {_show(fallback)}", file=sys.stderr)
            merged[key] = fallback
    return merged


def parse(text, keep_numbers=False):
    """The settings file's text as a dict, or None if it is not a JSON
    object. keep_numbers keeps each number as written (see Number)."""
    hooks = {"parse_float": Number, "parse_int": Number, "parse_constant": Number} if keep_numbers else {}
    try:
        data = json.loads(text, **hooks)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read(path=None, keep_numbers=False, warn=True):
    """The config as stored, defaulted and cleaned (see the module doc). A
    missing or unreadable file reads as the defaults."""
    try:
        data = parse(Path(path or CONFIG_FILE).read_text(), keep_numbers)
    except OSError:
        data = None
    return clean(data if data is not None else {}, warn)


def load(path=None, warn=True):
    """The config a run uses (see the module doc)."""
    config = read(path, warn=warn)
    override = os.environ.get("ASTRO_ARC_PROVIDER")
    if fits("provider", override):
        config["provider"] = override
    provider = config["provider"]
    for key, spec in schema()["keys"].items():
        empty = spec.get("empty")
        if empty and config[key] == "":
            config[key] = _provider_value(empty.split(".", 1)[1], provider)
    reasoning = os.environ.get("ASTRO_ARC_REASONING")
    if fits("chatReasoning", reasoning):
        config["chatReasoning"] = reasoning
    return config


def value(config, key):
    """`key` from a config dict as a run uses it, whatever the dict holds:
    its value if it fits, else the default, with "" resolved where the
    schema says what it means. For code handed a config it did not load (a
    harness's own, a test's partial one)."""
    provider = config.get("provider") if fits("provider", config.get("provider")) else "openai"
    v = config.get(key)
    if not fits(key, v):
        return default(key, provider)
    empty = schema()["keys"][key].get("empty")
    if empty and v == "":
        return _provider_value(empty.split(".", 1)[1], provider)
    return _num(v) if isinstance(v, Number) else v


def fixed_size(config, fallback="1600x900"):
    """(width, height) for a harness render: the configured backgroundSize,
    or `fallback` when it is "auto" (only astro-arc-generate can ask the
    monitor) or unset."""
    size = value(config, "backgroundSize")
    w, h = (size if size != "auto" else fallback).split("x")
    return int(w), int(h)


# ---- jq-compatible JSON ---------------------------------------------------

def _string(text):
    # jq escapes DEL as well as the control characters json does.
    return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")


def dumps(value, indent=2, _level=0):
    """`value` as jq prints it: two-space indent, `"key": value`, empty
    containers as {} and [], non-ASCII as itself, Numbers as written.
    indent=None gives jq -c's compact form."""
    pad = "" if indent is None else "\n" + " " * (indent * (_level + 1))
    end = "" if indent is None else "\n" + " " * (indent * _level)
    sep = ":" if indent is None else ": "
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = [f"{pad}{_string(k)}{sep}{dumps(v, indent, _level + 1)}" for k, v in value.items()]
        return "{" + ",".join(items) + end + "}"
    if isinstance(value, list):
        if not value:
            return "[]"
        return "[" + ",".join(f"{pad}{dumps(v, indent, _level + 1)}" for v in value) + end + "]"
    if isinstance(value, Number):
        return value.text
    if isinstance(value, str):
        return _string(value)
    return json.dumps(value)
