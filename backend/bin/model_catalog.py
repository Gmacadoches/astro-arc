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


# ---- Per-stage chat cost ------------------------------------------------
#
# An image render's cost is a property of (model, quality, size). A chat stage's
# is not: the token count is a property of the STAGE — how long that stage's
# prompt and reply run — and the price is a property of the MODEL. Splitting them
# is what lets a model that has never been used still get a real estimate:
# measured tokens for the stage, times that model's published rate.
#
# So a cost here is one of three things, and the UI always says which:
#   "actual" — this exact model has run this exact stage; its own measured tokens.
#   "est"    — measured tokens from whatever model HAS run this stage, priced at
#              this model's rate. Token counts barely move between models for the
#              same prompt, so this is a real projection, not a guess.
#   None     — nobody has ever run this stage, or this model has no rate.

STAGES = ("stage1", "stage15", "stage2", "signature")


def record_stage_usage(stage, model, usage):
    """Called after every chat call. Cheap, and never allowed to break a run."""
    if not usage or stage not in STAGES:
        return None
    prompt_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
    if not completion_tokens:
        return None
    learned = load_learned_usage()
    stages = learned.setdefault("stages", {})
    stages.setdefault(stage, {})[model] = {
        "inputTokens": prompt_tokens or 0,
        "outputTokens": completion_tokens,
        "measuredAt": datetime.now(timezone.utc).isoformat(),
    }
    LEARNED_USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    LEARNED_USAGE_FILE.write_text(json.dumps(learned, indent=2))
    return stages[stage][model]


def _representative_stage_tokens(stage, learned):
    """Token counts for a stage from whichever model most recently ran it.

    Used to price models that have never run it themselves. Picking the most
    recent rather than averaging is deliberate: prompt lengths change when the
    prompts themselves are edited, and an average would blend the old shape with
    the new one indefinitely.

    Falls back to another stage's measurement when this one has none, and that
    fallback is not a nicety — Stage 1 and Stage 1.5 are cached per period, so on
    most runs they never execute and never get measured. Without the fallback
    every preset showed "cost unknown" indefinitely despite Stage 2 being
    measured, which is worse than an approximation: it hides a number that is
    roughly right behind a number that is absent. The three chat stages send
    prompts of the same order of magnitude, so one stands in for another
    acceptably — and anything priced this way is labelled "est", never "actual".
    """
    stages = learned.get("stages") or {}
    by_model = stages.get(stage) or {}
    if by_model:
        return max(by_model.values(), key=lambda e: e.get("measuredAt", ""))
    everything = [e for other in stages.values() for e in other.values()]
    if not everything:
        return None
    return max(everything, key=lambda e: e.get("measuredAt", ""))


def stage_cost(stage, model, rates=None, learned=None):
    """(cost, source) for one call of `stage` on `model`. source is "actual",
    "est", or None when it genuinely cannot be known."""
    rates = rates if rates is not None else load_rates()
    learned = learned if learned is not None else load_learned_usage()
    rate = rates.get("chat", {}).get(model)
    if not rate:
        return None, None
    own = ((learned.get("stages") or {}).get(stage) or {}).get(model)
    entry, source = (own, "actual") if own else (_representative_stage_tokens(stage, learned), "est")
    if not entry:
        return None, None
    cost = round(
        entry["inputTokens"] * rate["input"] / 1_000_000
        + entry["outputTokens"] * rate["output"] / 1_000_000,
        6,
    )
    return cost, source


# gpt-image-1's published output-token counts per quality tier at 1024x1024 —
# the only model OpenAI documents this for. Applied to any model's own rate to
# project a render that has never happened. This is the ONE genuinely assumed
# number in the whole cost path, and it is why an unmeasured image cost is always
# labelled "est": the rate is published, the token count is borrowed.
_TIER_OUTPUT_TOKENS = {"low": 272, "medium": 1056, "high": 4160, "auto": 4160}
# Prompt lengths here run long; this is the observed order of magnitude, and it
# is a small share of an image call either way.
_TIER_INPUT_TOKENS = 320


def image_cost(model, quality, size, rates=None, learned=None):
    """(cost, source) for one render. "actual" once that exact combination has
    been measured, "est" from published rate x borrowed tier tokens before that,
    None when the model has no rate at all."""
    rates = rates if rates is not None else load_rates()
    learned = learned if learned is not None else load_learned_usage()
    measured = measured_cost(model, quality, size, rates, learned)
    if measured is not None:
        return measured, "actual"
    rate = rates.get("image", {}).get(model)
    tokens = _TIER_OUTPUT_TOKENS.get(quality)
    if not rate or not tokens:
        return None, None
    # Tier counts are per 1024x1024; scale by the real render area.
    try:
        w, h = (int(x) for x in size.split("x"))
        scale = (w * h) / (1024 * 1024)
    except (ValueError, AttributeError):
        scale = 1.0
    cost = (
        _TIER_INPUT_TOKENS * rate["input"] / 1_000_000
        + tokens * scale * rate["output"] / 1_000_000
    )
    return round(cost, 6), "est"


def run_cost(chat_model, image_model, quality, size, rates=None, learned=None):
    """Projected cost of one whole generation: the three chat stages plus the
    render. `source` is "actual" only when EVERY part of it was measured for the
    exact model in question — one estimated part makes the whole thing an
    estimate, because calling a mixed figure "actual" would be a lie about the
    weakest component.
    """
    rates = rates if rates is not None else load_rates()
    learned = learned if learned is not None else load_learned_usage()
    total, source = 0.0, "actual"
    for stage in ("stage1", "stage15", "stage2"):
        cost, st = stage_cost(stage, chat_model, rates, learned)
        if cost is None:
            return None, None
        total += cost
        if st != "actual":
            source = "est"
    image, image_source = image_cost(image_model, quality, size, rates, learned)
    if image is None:
        return None, None
    if image_source != "actual":
        source = "est"
    return round(total + image, 6), source


# ---- Discovered capabilities --------------------------------------------
#
# Some models reject `temperature` at anything but the default: measured on
# 2026-09-12, gpt-5-mini, gpt-5.6-luna and gpt-6-astra all refuse it while
# gpt-4.1, gpt-4o-mini and gpt-5.4 accept it. That does NOT follow family lines,
# so a hand-written capability table would be guesswork that goes stale. It is
# discovered at runtime instead — the rejection is a 400, which bills nothing —
# and cached here so the wasted first attempt happens once per model rather than
# once per call.

def get_capability(model, name, learned=None):
    learned = learned if learned is not None else load_learned_usage()
    return ((learned.get("capabilities") or {}).get(model) or {}).get(name)


def set_capability(model, name, value):
    learned = load_learned_usage()
    learned.setdefault("capabilities", {}).setdefault(model, {})[name] = value
    LEARNED_USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    LEARNED_USAGE_FILE.write_text(json.dumps(learned, indent=2))
    return value


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


SETTINGS_PATH = Path.home() / ".local/state/omarchy/settings/astro-arc.json"


def load_settings():
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _period_key(frequency, now_local=None):
    """Mirrors astro_engine.derive_arc's key, which is what the caches are keyed
    on. Duplicated rather than imported because importing the engine drags in
    swisseph and PIL to answer a question about the calendar."""
    now_local = now_local or datetime.now().astimezone()
    if frequency == "weekly":
        return now_local.strftime("%G-W%V")
    if frequency == "monthly":
        return now_local.strftime("%Y-%m")
    return now_local.strftime("%Y-%m-%d")


def _cache_is_valid(path, natal_hash, schema_version, required_key):
    try:
        cached = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    return bool(
        cached.get(required_key)
        and cached.get("natalHash") == natal_hash
        and cached.get("schemaVersion") == schema_version
    )


def next_run_cost(width=1024, height=1024, settings=None):
    """What the NEXT generation will actually cost, and what a generation costs
    when nothing is cached.

    These are different numbers and conflating them is what made the panel's
    figures misleading. Stage 1 and Stage 1.5 are cached per period and the
    visual signature per birth chart, so re-running the same day charges for
    Stage 2 and the render only — which is why the last run came in at $0.069
    while a fresh day of the same settings costs nearly three times that. The
    cost log averages real runs, so with several same-day regenerations in it,
    it reports something far below the true daily cost.

    Returns both: `next` for the Regenerate button, `full` for anything
    projecting a daily/weekly/monthly spend.

    `full` deliberately EXCLUDES the visual signature. The signature is cached
    per birth chart rather than per period, so it is paid once in the life of a
    chart and never again by a new day — counting it in a daily projection
    inflates every recurring figure. It is still reported in `stages` (and in
    `oneTime`) so it is visible rather than silently dropped.
    """
    settings = settings if settings is not None else load_settings()
    rates, learned = load_rates(), load_learned_usage()

    chat_stage1 = settings.get("stage1Model") or "gpt-4o-mini"
    chat_stage2 = settings.get("stage2Model") or "gpt-4o-mini"
    image_model = settings.get("openaiModel") or "gpt-image-1-mini"
    quality = settings.get("openaiQuality") or "medium"
    size = render_size_for(width, height)
    period_key = _period_key(settings.get("frequency") or "daily")

    # Imported here, not at module scope: cost_estimate imports this module, so
    # a top-level import of llm_pipeline would close the loop.
    try:
        from llm_pipeline import (  # noqa: PLC0415
            SIGNATURE_SCHEMA_VERSION, STAGE1_SCHEMA_VERSION,
            STAGE15_SCHEMA_VERSION, _natal_hash,
        )
        natal_hash = _natal_hash(settings)
        cached_stage1 = _cache_is_valid(
            STATE_DIR / "pipeline/readings" / f"{period_key}.json",
            natal_hash, STAGE1_SCHEMA_VERSION, "reading")
        cached_stage15 = _cache_is_valid(
            STATE_DIR / "pipeline/amplifications" / f"{period_key}.json",
            natal_hash, STAGE15_SCHEMA_VERSION, "constellation")
        cached_signature = _cache_is_valid(
            STATE_DIR / "pipeline/visual_signature.json",
            natal_hash, SIGNATURE_SCHEMA_VERSION, "signature")
        cache_known = True
    except Exception:  # noqa: BLE001 — a cost readout must not break on this
        cached_stage1 = cached_stage15 = cached_signature = False
        cache_known = False

    image, image_source = image_cost(image_model, quality, size, rates, learned)
    parts, next_total, full_total, source = {}, 0.0, 0.0, "actual"

    for name, model, is_cached in (
        ("stage1", chat_stage1, cached_stage1),
        ("stage15", chat_stage1, cached_stage15),   # Stage 1.5 runs on Stage 1's model
        ("stage2", chat_stage2, False),             # always runs
        ("signature", chat_stage2, cached_signature),
    ):
        cost, src = stage_cost(name if name != "signature" else "signature", model, rates, learned)
        parts[name] = {"model": model, "cached": is_cached, "cost": cost, "source": src}
        if cost is None:
            next_total = full_total = None
            source = None
            continue
        if next_total is not None:
            # Signature excluded from `full` — see the docstring.
            if name != "signature":
                full_total += cost
            if not is_cached:
                next_total += cost
            if src != "actual":
                source = "est"

    if next_total is not None and image is not None:
        next_total += image
        full_total += image
        if image_source != "actual":
            source = "est"
    else:
        next_total = full_total = None
        source = None

    signature_cost = (parts.get("signature") or {}).get("cost")
    return {
        "periodKey": period_key,
        "cacheKnown": cache_known,
        "stages": parts,
        # One-time per birth chart, not per period. Broken out so a projection
        # can exclude it without pretending it doesn't exist.
        "oneTime": {"signature": signature_cost, "paid": cached_signature},
        "image": {"model": image_model, "quality": quality, "cost": image, "source": image_source},
        "next": None if next_total is None else round(next_total, 6),
        "full": None if full_total is None else round(full_total, 6),
        "source": source,
    }


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
    shortlist_chat = set(rates.get("shortlist", {}).get("chat", []))
    shortlist_image = set(rates.get("shortlist", {}).get("image", []))

    chat_rows, image_rows = [], []
    for model_id in available:
        if _SNAPSHOT_RE.search(model_id):
            continue
        kind = classify(model_id)
        if kind == "chat":
            rate = rates["chat"].get(model_id)
            # Per-stage cost for THIS model whether or not it has ever run:
            # measured tokens for the stage priced at this model's rate. See
            # stage_cost for what "actual" vs "est" mean.
            stages = {}
            run_total, run_source = 0.0, "actual"
            for stage in ("stage1", "stage15", "stage2"):
                cost, src = stage_cost(stage, model_id, rates, learned)
                stages[stage] = {"cost": cost, "source": src}
                if cost is None:
                    run_total, run_source = None, None
                elif run_total is not None:
                    run_total += cost
                    if src != "actual":
                        run_source = "est"
            chat_rows.append({
                "model": model_id,
                "label": _label_for(model_id, rate),
                "hasRate": rate is not None,
                "inputRate": (rate or {}).get("input"),
                "outputRate": (rate or {}).get("output"),
                "shortlisted": model_id in shortlist_chat,
                "stages": stages,
                "chatRunCost": None if run_total is None else round(run_total, 6),
                "chatRunSource": run_source,
            })
        elif kind == "image":
            rate = rates["image"].get(model_id)
            qualities = rates["image_qualities"].get(model_id, DEFAULT_IMAGE_QUALITIES)
            for quality in qualities:
                size = render_size_for(width, height)
                cost, cost_source = image_cost(model_id, quality, size, rates, learned)
                if cost_source == "actual":
                    cost_state = "measured"
                elif cost_source == "est":
                    cost_state = "estimated"
                else:
                    cost_state = "no-rate"
                label = _label_for(model_id, rate)
                image_rows.append({
                    "model": model_id,
                    "quality": quality,
                    "label": label if quality == "auto" else f"{label} — {quality.capitalize()}",
                    "hasRate": rate is not None,
                    "cost": cost,
                    "costState": cost_state,
                    "shortlisted": model_id in shortlist_image,
                })

    def rank(rows, order, key):
        index = {m: i for i, m in enumerate(order)}
        # Unlisted models sort last so a newly-released one is never picked
        # ahead of one that has actually been vetted.
        rows.sort(key=lambda r: (index.get(r[key], len(order)), r[key]))
        return rows

    # Presets, priced exactly like everything else. A preset naming a model this
    # key cannot reach is marked unavailable rather than hidden, so a short tier
    # list never leaves you wondering what happened to the rest.
    available_set = set(available)
    presets = []
    for key, row in (rates.get("presets") or {}).items():
        chat_model, image_model = row.get("chat"), row.get("image")
        quality = row.get("quality", "medium")
        cost, source = run_cost(chat_model, image_model, quality, render_size_for(width, height), rates, learned)
        presets.append({
            "key": key,
            "label": row.get("label", key.capitalize()),
            "chat": chat_model,
            "image": image_model,
            "quality": quality,
            "available": chat_model in available_set and image_model in available_set,
            "runCost": cost,
            "runSource": source,
        })
    # Ordered by cost where known, unpriced last, so the list reads as a ramp
    # even before every tier has been measured.
    presets.sort(key=lambda p: (p["runCost"] is None, p["runCost"] or 0))

    return {
        "nextRun": next_run_cost(width, height),
        "presets": presets,
        "fetchedAt": cached.get("fetchedAt"),
        "stale": cache_is_stale(cached),
        "everFetched": bool(cached),
        "chat": rank(chat_rows, rates["hierarchy"].get("chat", []), "model"),
        "image": rank(image_rows, rates["hierarchy"].get("image", []), "model"),
        "ratesPath": str(PIPELINE_DIR / "model-rates.toml"),
    }


def main():
    args = sys.argv[1:]
    # astro-arc-generate asks for this before deciding whether it can afford a
    # quality bump. Prints a number, or "null" when the cost is genuinely not
    # known — the caller must treat null as "unknown", never as "free".
    if args and args[0] == "--measured-cost":
        _, model, quality, size = args[:4]
        cost = measured_cost(model, quality, size)
        print("null" if cost is None else cost)
        return
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
