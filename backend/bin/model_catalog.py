#!/usr/bin/env python3
"""The one place that knows which models exist, what they cost, and what they
actually consumed. Everything else — cost_estimate.py, openai_image_gen.py,
llm_pipeline.py and the widget's three model dropdowns — reads from here.

Before this existed the same facts were duplicated in four places that had to
be kept in agreement by hand (cost_estimate.CHAT_MODEL_RATES,
cost_estimate.IMAGE_MODEL_RATES, openai_image_gen.MODEL_COSTS, and Model.js's
OPENAI_MODEL_CHOICES), and they had already drifted: gpt-image-1 was priced in
one of them and offered by none. Worse, the widget's list was a hardcoded four
rows while the key in use could actually reach ten image models and sixty-five
chat models, so most of what had been paid for was unreachable from the UI.

THREE KINDS OF KNOWLEDGE, THREE DIFFERENT SOURCES. Keeping them apart is the
whole design:

  1. AVAILABILITY — which models this key can reach. Discovered from
     GET /v1/models, cached to disk, refreshed on demand or when stale. Free,
     no tokens billed.
  2. PRICE — dollars per token. NOT available from the API on a normal key at
     all (the Admin-API route was removed from this project on 2026-09-06 for
     that exact reason). Hand-typed into pipeline/model-rates.toml. A model
     with no rate is still selectable and still runs; it reports "cost
     unknown" rather than a fabricated number.
  3. USAGE — how many tokens a render actually consumed, per model/quality/
     size. Measured from real API responses and written to the learned-usage
     file. This replaces the weakest guess in the old pricing path, which
     applied gpt-image-1's published per-tier token counts to other models'
     rates (see PRICE_ESTIMATE_ASSUMPTION) — an estimate resting on an
     estimate. Once a combination has been rendered once, its token count is
     measured and only the hand-typed rate remains an input.

So an unpriced model becomes fully costed after (a) one real render, which
measures the tokens, and (b) two numbers added to model-rates.toml. Neither
half can be skipped: tokens cannot be priced without a rate, and a rate alone
still needs to know how many tokens a tier burns.
"""

import json
import re
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

PIPELINE_DIR = Path.home() / ".local/share/omarchy/astro-arc/pipeline"
STATE_DIR = Path.home() / ".local/state/omarchy/astro-arc"
AVAILABILITY_CACHE = STATE_DIR / "model-availability.json"
LEARNED_USAGE_FILE = STATE_DIR / "model-usage.json"

# How old the availability cache may get before the widget refreshes it on its
# own. The call is free and takes about a second, so this only exists to avoid
# hitting the network every single time the settings panel opens.
CACHE_TTL = timedelta(hours=24)

DEFAULT_IMAGE_QUALITIES = ["low", "medium", "high"]

# Dated snapshots (gpt-4.1-2025-04-14) duplicate their own alias and would
# double the length of every dropdown for no benefit — the alias is what you
# want unless you are pinning a specific build, and anyone pinning one can type
# it into model-rates.toml. Filtered from the offered list, never from pricing.
# Two snapshot spellings in the wild: gpt-4.1-2025-04-14 and gpt-3.5-turbo-0125.
_SNAPSHOT_RE = re.compile(r"(-\d{4}-\d{2}-\d{2}|-\d{4})$")

# Classification is by id, because GET /v1/models returns almost nothing else
# useful — no modality, no capability flags, no pricing.
_IMAGE_RE = re.compile(r"(image|dall-e)", re.I)
_CHAT_RE = re.compile(r"^(gpt-\d|o\d|chatgpt)", re.I)
# Models that match _CHAT_RE but cannot serve this pipeline's JSON chat calls.
_NOT_CHAT_RE = re.compile(
    r"(audio|realtime|transcribe|tts|search|embedding|moderation|image|dall-e)", re.I
)


def load_rates():
    """pipeline/model-rates.toml, read fresh every call so a hand edit takes
    effect on the next generation with no restart — same contract as
    registers.toml and styles.toml."""
    path = PIPELINE_DIR / "model-rates.toml"
    if not path.exists():
        return {"chat": {}, "image": {}, "image_qualities": {}, "hierarchy": {}}
    with open(path, "rb") as f:
        data = tomllib.load(f)
    for key in ("chat", "image", "image_qualities", "hierarchy"):
        data.setdefault(key, {})
    return data


def classify(model_id):
    """'image', 'chat', or None. See the regexes above for why this is done by
    id rather than by asking the API."""
    if _IMAGE_RE.search(model_id):
        return "image"
    if _CHAT_RE.match(model_id) and not _NOT_CHAT_RE.search(model_id):
        return "chat"
    return None


def load_availability():
    """The cached model list, or None if never fetched. Never raises — a
    missing or corrupt cache just means 'refresh needed', not a failure."""
    if not AVAILABILITY_CACHE.exists():
        return None
    try:
        return json.loads(AVAILABILITY_CACHE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def cache_is_stale(cached):
    if not cached or not cached.get("fetchedAt"):
        return True
    try:
        fetched = datetime.fromisoformat(cached["fetchedAt"])
    except ValueError:
        return True
    return datetime.now(timezone.utc) - fetched > CACHE_TTL


def refresh_availability():
    """GET /v1/models -> cache. Free (no tokens billed) and about a second.

    Imported lazily so that merely reading the catalog never touches the
    keyring: get_api_key shells out to secret-tool, and the widget calls this
    module on every panel open.
    """
    from openai_image_gen import _request  # noqa: PLC0415 — see docstring

    data = _request("/models")
    ids = sorted(m["id"] for m in data.get("data", []))
    payload = {
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "models": ids,
    }
    AVAILABILITY_CACHE.parent.mkdir(parents=True, exist_ok=True)
    AVAILABILITY_CACHE.write_text(json.dumps(payload, indent=2))
    return payload


def load_learned_usage():
    if not LEARNED_USAGE_FILE.exists():
        return {}
    try:
        return json.loads(LEARNED_USAGE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def usage_key(model, quality, size):
    """Token consumption depends on all three, so a measurement is only valid
    for the exact combination it was measured on."""
    return f"{model}|{quality}|{size}"


def record_usage(model, quality, size, usage):
    """Store what a real call actually consumed. Called after a successful
    render; never fabricates, and silently does nothing if the API reported no
    usage block at all."""
    if not usage:
        return None
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    if not output_tokens:
        return None
    learned = load_learned_usage()
    learned[usage_key(model, quality, size)] = {
        "inputTokens": input_tokens or 0,
        "outputTokens": output_tokens,
        "measuredAt": datetime.now(timezone.utc).isoformat(),
    }
    LEARNED_USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    LEARNED_USAGE_FILE.write_text(json.dumps(learned, indent=2))
    return learned[usage_key(model, quality, size)]


def measured_cost(model, quality, size, rates=None, learned=None):
    """Dollars for one render of this exact combination, from MEASURED tokens
    times a hand-entered rate. Returns None when either half is missing, which
    the UI shows as "cost unknown" — never a guess."""
    rates = rates if rates is not None else load_rates()
    learned = learned if learned is not None else load_learned_usage()
    entry = learned.get(usage_key(model, quality, size))
    rate = rates.get("image", {}).get(model) or rates.get("chat", {}).get(model)
    if not entry or not rate:
        return None
    return round(
        entry["inputTokens"] * rate["input"] / 1_000_000
        + entry["outputTokens"] * rate["output"] / 1_000_000,
        6,
    )


def render_size_for(width, height):
    """The size an image model will ACTUALLY render at for a given target.

    openai_image_gen renders only at OPENAI_IMAGE_SIZES and crops to the target
    afterwards, so a 1600x900 background is a 1536x1024 render. Token usage
    tracks the render size, so measurements must be keyed on it — keying on the
    target instead would leave record_usage and the catalog permanently
    disagreeing, and every row would read "not yet rendered" forever.
    """
    from image_fit import closest_supported_size  # noqa: PLC0415 — pulls in PIL
    from openai_image_gen import OPENAI_IMAGE_SIZES  # noqa: PLC0415

    return closest_supported_size(width, height, OPENAI_IMAGE_SIZES)


def _label_for(model_id, rate_row):
    return (rate_row or {}).get("label") or model_id


def catalog(width=1024, height=1024):
    """Everything the widget needs, in one JSON-able dict.

    Every available model is listed whether or not it has a rate. `cost` is
    None for anything unpriced or not yet measured, and `costState` says which
    of the two is missing so the UI can tell the user what to do about it
    rather than just showing a blank.
    """
    rates = load_rates()
    cached = load_availability() or {}
    learned = load_learned_usage()
    available = cached.get("models") or []

    chat_rows, image_rows = [], []
    for model_id in available:
        if _SNAPSHOT_RE.search(model_id):
            continue
        kind = classify(model_id)
        if kind == "chat":
            rate = rates["chat"].get(model_id)
            chat_rows.append({
                "model": model_id,
                "label": _label_for(model_id, rate),
                "hasRate": rate is not None,
                "inputRate": (rate or {}).get("input"),
                "outputRate": (rate or {}).get("output"),
            })
        elif kind == "image":
            rate = rates["image"].get(model_id)
            qualities = rates["image_qualities"].get(model_id, DEFAULT_IMAGE_QUALITIES)
            for quality in qualities:
                size = render_size_for(width, height)
                cost = measured_cost(model_id, quality, size, rates, learned)
                if cost is not None:
                    cost_state = "measured"
                elif rate is None:
                    cost_state = "no-rate"
                else:
                    cost_state = "not-yet-rendered"
                label = _label_for(model_id, rate)
                image_rows.append({
                    "model": model_id,
                    "quality": quality,
                    "label": label if quality == "auto" else f"{label} — {quality.capitalize()}",
                    "hasRate": rate is not None,
                    "cost": cost,
                    "costState": cost_state,
                })

    def rank(rows, order, key):
        index = {m: i for i, m in enumerate(order)}
        # Unlisted models sort last so a newly-released one is never picked
        # ahead of one that has actually been vetted.
        rows.sort(key=lambda r: (index.get(r[key], len(order)), r[key]))
        return rows

    return {
        "fetchedAt": cached.get("fetchedAt"),
        "stale": cache_is_stale(cached),
        "everFetched": bool(cached),
        "chat": rank(chat_rows, rates["hierarchy"].get("chat", []), "model"),
        "image": rank(image_rows, rates["hierarchy"].get("image", []), "model"),
        "ratesPath": str(PIPELINE_DIR / "model-rates.toml"),
    }


def main():
    args = sys.argv[1:]
    if "--refresh" in args:
        try:
            refresh_availability()
        except Exception as exc:  # noqa: BLE001 — the widget needs a reason, not a traceback
            print(json.dumps({"ok": False, "error": str(exc)[:300]}))
            sys.exit(1)
    width = height = 1024
    for i, a in enumerate(args):
        if a == "--size" and i + 1 < len(args):
            width, height = (int(x) for x in args[i + 1].split("x"))
    result = catalog(width, height)
    result["ok"] = True
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
