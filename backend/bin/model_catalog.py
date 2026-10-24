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

OPENROUTER IS EASIER ON TWO OF THE THREE. Its model listings are public and
carry prices, so availability AND price both come from the API and nothing is
hand-typed (model-rates.toml only holds its presets, ordering and shortlist,
under [openrouter]). Some image models there are priced per image rather than
per token, which needs no token count at all. And every response reports
usage.cost, the dollars actually billed, which record_usage keeps as the
measured cost. The one gap left: a non-OpenAI image model priced per token
(the Gemini image models) has no published tokens-per-image, so it reads
"cost known after first render" until one render measures it, rather than borrowing
gpt-image-1's counts for a different vendor's tokenizer.
"""

import json
import os
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import STATE_DIR  # noqa: E402
from period_key import cache_key  # noqa: E402  — one definition, see period_key.py
import settings as settings_schema  # noqa: E402  — the module; `settings` below is a config dict
from providers import provider_of, reasoning_of  # noqa: E402

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
AVAILABILITY_CACHE = STATE_DIR / "model-availability.json"


# Every function below that depends on the provider takes it as an argument.
# "openai" is the default because it is what each one did before OpenRouter;
# a caller with a run config passes providers.provider_of(config).

def availability_cache(provider="openai"):
    """One cache per provider, so switching back and forth never shows the
    other provider's models. OpenAI keeps the original filename."""
    if provider == "openai":
        return AVAILABILITY_CACHE
    return STATE_DIR / f"model-availability-{provider}.json"
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


def load_rates(provider="openai"):
    """pipeline/model-rates.toml, read fresh every call so a hand edit takes
    effect on the next generation with no restart — same contract as
    registers.toml and the theme files.

    For OpenRouter the rates themselves come from the cached model listing
    (see refresh_availability) and the TOML's [openrouter] table supplies only
    presets, hierarchy and shortlist. Same shape either way, so every caller
    below prices both providers with the same code."""
    path = PIPELINE_DIR / "model-rates.toml"
    data = {}
    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)
    if provider == "openrouter":
        data = _openrouter_rates(data.get("openrouter") or {})
    else:
        data.pop("openrouter", None)
    for key in ("chat", "image", "image_qualities", "hierarchy"):
        data.setdefault(key, {})
    return data


def _openrouter_rates(toml_section):
    """Rates in this module's own shape ($ per 1M tokens), built from the
    cached OpenRouter listing. Per-image prices go under `perImage`, keyed by
    the variant OpenRouter names ("1k", "low_1k"), or "" when there is one."""
    from openrouter_image_gen import tiers_for  # noqa: PLC0415

    data = dict(toml_section)
    cached = load_availability("openrouter") or {}
    chat = {}
    for model_id, row in (cached.get("chatModels") or {}).items():
        if row.get("input") is not None and row.get("output") is not None:
            chat[model_id] = {"label": row.get("label"), "input": row["input"], "output": row["output"]}
    image, qualities = {}, {}
    for model_id, row in (cached.get("imageModels") or {}).items():
        qualities[model_id] = tiers_for(row.get("supported"))
        rate = {"label": row.get("label")}
        for price in row.get("pricing") or []:
            if price.get("billable") != "output_image" and price.get("unit") == "image":
                continue  # an image-to-image input charge; never sent here
            per = price.get("cost_usd")
            if per is None:
                continue
            if price.get("unit") == "token":
                field = {"input_text": "input", "output_image": "output", "input_image": "image_input"}.get(price.get("billable"))
                if field:
                    rate[field] = round(per * 1_000_000, 6)
            elif price.get("unit") == "image":
                rate.setdefault("perImage", {})[(price.get("variant") or "").lower()] = per
        if "output" in rate:
            rate.setdefault("input", 0.0)
        if "output" in rate or "perImage" in rate:
            image[model_id] = rate
    data["chat"], data["image"], data["image_qualities"] = chat, image, qualities
    return data


def classify(model_id, cached=None):
    """'image', 'chat', or None. See the regexes above for why this is done by
    id rather than by asking the API. OpenRouter's listing does say, so its
    cache records the kind directly and that wins when it is there."""
    if cached and "imageModels" in cached:
        if model_id in cached["imageModels"]:
            return "image"
        return "chat" if model_id in (cached.get("chatModels") or {}) else None
    if _IMAGE_RE.search(model_id):
        return "image"
    if _CHAT_RE.match(model_id) and not _NOT_CHAT_RE.search(model_id):
        return "chat"
    return None


def load_availability(provider="openai"):
    """The cached model list, or None if never fetched. Never raises — a
    missing or corrupt cache just means 'refresh needed', not a failure."""
    path = availability_cache(provider)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
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


def refresh_availability(provider="openai"):
    """GET /v1/models -> cache. Free (no tokens billed) and about a second.

    Imported lazily so that merely reading the catalog never touches the
    keyring: get_api_key shells out to secret-tool, and the widget calls this
    module on every panel open.
    """
    from openai_image_gen import _request  # noqa: PLC0415 — see docstring

    if provider == "openrouter":
        payload = _refresh_openrouter(lambda path, **kw: _request(path, provider="openrouter", **kw))
    else:
        data = _request("/models")
        ids = sorted(m["id"] for m in data.get("data", []))
        payload = {
            "fetchedAt": datetime.now(timezone.utc).isoformat(),
            "models": ids,
        }
    path = availability_cache(provider)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return payload


def _refresh_openrouter(request):
    """Both of OpenRouter's listings, public and free, so no key is sent and a
    fresh install can browse models and prices before pasting one.

    Chat models are kept only when they output text alone and accept
    response_format: every chat stage here asks for a JSON object, and a
    model that ignores the request breaks the parse downstream. That trims
    the listing from roughly 470 to the ones that can actually do the job."""
    from openrouter_image_gen import fetch_image_models  # noqa: PLC0415

    chat = {}
    for m in request("/models", auth=False).get("data", []):
        arch = m.get("architecture") or {}
        params = m.get("supported_parameters") or []
        if arch.get("output_modalities") != ["text"] or "response_format" not in params:
            continue
        # The :batch twins are the same model on a deferred queue, and this
        # pipeline waits on every reply.
        if m["id"].endswith(":batch"):
            continue
        pricing = m.get("pricing") or {}
        try:
            input_rate = float(pricing.get("prompt")) * 1_000_000
            output_rate = float(pricing.get("completion")) * 1_000_000
        except (TypeError, ValueError):
            input_rate = output_rate = None
        # OpenRouter quotes -1 for "varies by route"; that is not a price.
        if input_rate is not None and (input_rate < 0 or output_rate < 0):
            input_rate = output_rate = None
        chat[m["id"]] = {
            "label": m.get("name") or m["id"],
            "input": None if input_rate is None else round(input_rate, 6),
            "output": None if output_rate is None else round(output_rate, 6),
            "temperature": "temperature" in params,
            # {mandatory, supported_efforts, default_effort} or None. See
            # providers.reasoning_for for how the chatReasoning setting uses it.
            "reasoning": (m.get("reasoning") or None) if "reasoning" in params else None,
        }
    images = fetch_image_models()
    return {
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "models": sorted(set(chat) | set(images)),
        "chatModels": chat,
        "imageModels": images,
    }


def load_learned_usage():
    if not LEARNED_USAGE_FILE.exists():
        return {}
    try:
        return json.loads(LEARNED_USAGE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_learned(learned):
    """Write the file whole, by rename. Written in place, a second process
    reading it mid-write got a partial file, load_learned_usage() read that
    as {}, and its next write replaced every measurement with one entry: two
    parallel astro-arc-style runs did exactly that on 2026-10-17."""
    LEARNED_USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEARNED_USAGE_FILE.with_name(f".{LEARNED_USAGE_FILE.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(learned, indent=2))
    tmp.replace(LEARNED_USAGE_FILE)


def usage_key(model, quality, size):
    """Token consumption depends on all three, so a measurement is only valid
    for the exact combination it was measured on."""
    return f"{model}|{quality}|{size}"


def record_usage(model, quality, size, usage):
    """Store what a real call actually consumed. Called after a successful
    render; never fabricates, and silently does nothing if the API reported no
    usage block at all.

    OpenRouter adds usage.cost, the dollars billed. It is kept beside the
    tokens and preferred by measured_cost, since it needs no rate at all, and
    it is the only measurement a per-image model reports (its token counts
    can be zero)."""
    if not usage:
        return None
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    billed = usage.get("cost")
    if not output_tokens and billed is None:
        return None
    learned = load_learned_usage()
    key = usage_key(model, quality, size)
    previous = learned.get(key) or {}
    if not output_tokens:
        output_tokens = previous.get("outputTokens") or 0
    # Keep a short history and project from the MEDIAN, because `auto` is not a
    # fixed tier: the same model at the same size was measured at 343, 601 and
    # 1372 output tokens on three runs, a 4x swing, and projecting from whichever
    # ran last made the High preset appear to cost anywhere between $1.34 and
    # $2.04 a month depending on nothing but recency. An explicit tier is stable
    # and this history simply confirms that; `auto` needs the median to be
    # predictable at all.
    history = (previous.get("history") or [])[-4:]
    history.append(output_tokens)
    learned[key] = {
        "inputTokens": input_tokens or 0,
        "outputTokens": sorted(history)[len(history) // 2],
        "lastOutputTokens": output_tokens,
        "history": history,
        "measuredAt": datetime.now(timezone.utc).isoformat(),
    }
    if billed is not None:
        # Median for the same reason as the tokens above.
        cost_history = (previous.get("costHistory") or [])[-4:] + [billed]
        learned[key]["costHistory"] = cost_history
        learned[key]["costUsd"] = sorted(cost_history)[len(cost_history) // 2]
    _write_learned(learned)
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

STAGES = ("stage1", "stage15", "stage2", "caption")


def stage_model_key(model, effort="default", cached=None):
    """What a stage measurement is filed under: the model id, plus the
    reasoning effort actually sent when one was ("anthropic/claude-haiku-5.5@none").

    Reasoning changes a stage's cost far more than the model does: on the
    2026-10-24 sweeps Haiku spent 3,551 output tokens on a Stage 2 with its
    default reasoning and 317 with it off. Filing both under the bare id meant
    whichever ran last priced the other, so a High preset using Haiku's
    default reasoning showed half its real cost after a reasoning-off sweep.

    Uses the effort that would really be SENT (providers.reasoning_for), so a
    model that rejects "none" and gets "low" instead is filed under "@low"."""
    if effort == "default":
        return model
    from providers import reasoning_for  # noqa: PLC0415

    if cached is None:
        cached = load_availability("openrouter") or {}
    sent = reasoning_for((cached.get("chatModels") or {}).get(model), effort)
    return f"{model}@{sent['effort']}" if sent else model


def record_stage_usage(stage, model, usage):
    """Called after every chat call. Cheap, and never allowed to break a run.
    `model` is the stage_model_key, so a reasoning effort is kept apart."""
    if not usage or stage not in STAGES:
        return None
    prompt_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
    if not completion_tokens:
        return None
    # Reasoning tokens are billed at the OUTPUT rate but never appear in the
    # response text, so without recording them a stage's cost is unexplainable:
    # gpt-6-astra billed 859 completion tokens for a 74-word prompt where
    # gpt-5.4 billed 136 for a 76-word one, and the ~720-token difference is
    # invisible anywhere else. Absent on models that don't reason.
    details = usage.get("completion_tokens_details") or {}
    reasoning = details.get("reasoning_tokens")
    learned = load_learned_usage()
    stages = learned.setdefault("stages", {})
    stages.setdefault(stage, {})[model] = {
        "inputTokens": prompt_tokens or 0,
        "outputTokens": completion_tokens,
        "reasoningTokens": reasoning,
        "measuredAt": datetime.now(timezone.utc).isoformat(),
    }
    _write_learned(learned)
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
        return _without_reasoning(max(by_model.values(), key=lambda e: e.get("measuredAt", "")))
    everything = [e for other in stages.values() for e in other.values()]
    if not everything:
        return None
    return _without_reasoning(max(everything, key=lambda e: e.get("measuredAt", "")))


def _without_reasoning(entry):
    """Strip reasoning tokens when a measurement stands in for another model.

    Reasoning is a property of the MODEL, not of the stage. gpt-6-astra spends
    ~734 reasoning tokens writing the same 74-word prompt gpt-5.4 writes with
    none, so lending astra's raw output count to gpt-4.1 priced gpt-4.1's stages
    as if it reasoned too — it pushed the High preset from $1.39 to $2.04 a
    month purely because a Maximum run happened to be the most recent
    measurement. The visible output is the part that belongs to the stage, so
    that is the part that is lent out.

    A model's own measurement is never stripped: when it is priced against
    itself, its reasoning is real cost it will genuinely incur.
    """
    if not entry:
        return entry
    reasoning = entry.get("reasoningTokens") or 0
    if not reasoning:
        return entry
    return dict(entry, outputTokens=max(entry["outputTokens"] - reasoning, 1))


def stage_cost(stage, model, rates=None, learned=None, effort="default"):
    """(cost, source) for one call of `stage` on `model`. source is "actual",
    "est", or None when it genuinely cannot be known. `effort` is the
    chatReasoning setting it would run under (see stage_model_key)."""
    rates = rates if rates is not None else load_rates()
    learned = learned if learned is not None else load_learned_usage()
    rate = rates.get("chat", {}).get(model)
    if not rate:
        return None, None
    own = ((learned.get("stages") or {}).get(stage) or {}).get(stage_model_key(model, effort))
    # `own` is used as measured, reasoning included — that is cost this model
    # really incurs. Only a BORROWED measurement gets its reasoning stripped.
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
    # openai/gpt-image-2.5-sunburst on OpenRouter is the same model as
    # gpt-image-2.5-sunburst on OpenAI, so tokens measured on either provider
    # predict the other far better than gpt-image-1's borrowed counts below.
    # Still "est": the tokens are measured, but not on this route.
    if rate and "output" in rate and model.startswith("openai/"):
        twin = learned.get(usage_key(model.split("/", 1)[1], quality, size))
        if twin and twin.get("outputTokens"):
            cost = (twin.get("inputTokens", 0) * rate.get("input", 0) + twin["outputTokens"] * rate["output"]) / 1_000_000
            return round(cost, 6), "est"
    if rate and "perImage" in rate:
        cost = _per_image_cost(model, quality, rate["perImage"], rates)
        return (cost, "est") if cost is not None else (None, None)
    tokens = _TIER_OUTPUT_TOKENS.get(quality)
    # gpt-image-1's tier tokens only stand in for OpenAI models. On OpenRouter
    # that means the openai/ ones; another vendor's tokenizer is not the same
    # unit, so its first render is what prices it.
    if "/" in model and not model.startswith("openai/"):
        tokens = None
    if not rate or not tokens or "output" not in rate:
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


def _per_image_cost(model, quality, prices, rates):
    """A per-image price for the variant this tier will actually request.
    OpenRouter names variants by what is sent ("1k", "low_1k"), so the
    variant is rebuilt from the same tier_params the render uses."""
    if "" in prices and len(prices) == 1:
        return prices[""]
    try:
        from openrouter_image_gen import tier_params  # noqa: PLC0415

        cached = (load_availability("openrouter") or {}).get("imageModels") or {}
        params = tier_params((cached.get(model) or {}).get("supported"), quality)
    except Exception:  # noqa: BLE001
        params = {}
    q, res = params.get("quality"), (params.get("resolution") or "").lower()
    for variant in ("_".join(x for x in (q, res) if x), res, q, ""):
        if variant is not None and variant in prices:
            return prices[variant]
    return None


def run_cost(chat_model, image_model, quality, size, rates=None, learned=None, effort="default"):
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
        cost, st = stage_cost(stage, chat_model, rates, learned, effort)
        if cost is None:
            return None, None
        # Stage 1.5 is two calls (meaning + material), see above.
        total += cost * (2 if stage == "stage15" else 1)
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
    _write_learned(learned)
    return value


def measured_cost(model, quality, size, rates=None, learned=None):
    """Dollars for one render of this exact combination, from MEASURED tokens
    times a hand-entered rate. Returns None when either half is missing, which
    the UI shows as "cost unknown" — never a guess."""
    rates = rates if rates is not None else load_rates()
    learned = learned if learned is not None else load_learned_usage()
    entry = learned.get(usage_key(model, quality, size))
    if entry and entry.get("costUsd") is not None:
        return round(entry["costUsd"], 6)
    rate = rates.get("image", {}).get(model) or rates.get("chat", {}).get(model)
    if not entry or not rate or "output" not in rate:
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
    from image_fit import closest_supported_size  # noqa: PLC0415
    from openai_image_gen import OPENAI_IMAGE_SIZES  # noqa: PLC0415

    return closest_supported_size(width, height, OPENAI_IMAGE_SIZES)




def load_settings():
    """The saved settings as a run uses them (settings.load)."""
    return settings_schema.load()


def _period_key(frequency, now_local=None):
    """Which cache key the next run will look for.

    That is `cache_key`, not `period_key`: the engine has no hourly arc, so the
    caches stay keyed by date however often an hourly schedule fires, while
    astro-arc-generate's own YYYY-MM-DDTHH key names output files. period_key.py
    owns both rules; this used to be a hand-kept copy of them."""
    return cache_key(frequency, now_local)


def next_run_cost(width=1024, height=1024, settings=None, state=None, catalog=None):
    """What the NEXT generation will actually cost, and what a generation costs
    when nothing is cached.

    These are different numbers and conflating them is what made the panel's
    figures misleading. Stage 1 and Stage 1.5's meaning are cached per period,
    so re-running the same day charges for
    Stage 2 and the render only — which is why the last run came in at $0.069
    while a fresh day of the same settings costs nearly three times that. The
    cost log averages real runs, so with several same-day regenerations in it,
    it reports something far below the true daily cost.

    Returns both: `next` for the Regenerate button, `full` for anything
    projecting a daily/weekly/monthly spend.

    The visual signature, a one-time call per birth chart that used to be
    reported here under `oneTime`, was removed from the pipeline on 2026-10-08.
    The caption check (one cheap judge call per Stage 2 attempt, since
    2026-09-28) is counted instead, as always running.

    Which stages are cached is asked of llm_pipeline.next_run_cached, the
    pipeline's own rule. Until 2026-10-24 this re-derived it without the
    style, the charter or Stage 1's distillation, so a style switch was
    quoted as a cache hit. `state` is the PipelineState to ask and `catalog`
    the themes to read (production's by default).
    """
    settings = settings if settings is not None else load_settings()
    provider = provider_of(settings)
    rates, learned = load_rates(provider), load_learned_usage()

    chat_stage1 = settings_schema.value(settings, "stage1Model")
    chat_stage2 = settings_schema.value(settings, "stage2Model")
    image_model = settings_schema.value(settings, "openaiModel")
    quality = settings_schema.value(settings, "openaiQuality")
    effort = reasoning_of(settings)
    size = render_size_for(width, height)
    period_key = _period_key(settings_schema.value(settings, "frequency"))

    # Imported here, not at module scope: cost_estimate imports this module, so
    # a top-level import of llm_pipeline would close the loop.
    try:
        from llm_pipeline import caption_model_for, next_run_cached  # noqa: PLC0415
        cached_stage1, cached_stage15 = next_run_cached(settings, period_key, state, catalog)
        caption_model = settings.get("captionModel") or caption_model_for(provider)
        cache_known = True
    except Exception:  # noqa: BLE001 — a cost readout must not break on this
        cached_stage1 = cached_stage15 = False
        caption_model = settings.get("captionModel") or settings_schema.default("stage1Model", provider)
        cache_known = False

    image, image_source = image_cost(image_model, quality, size, rates, learned)
    parts, next_total, full_total, source = {}, 0.0, 0.0, "actual"

    for name, model, is_cached in (
        ("stage1", chat_stage1, cached_stage1),
        ("stage15", chat_stage1, cached_stage15),   # Stage 1.5 runs on Stage 1's model
        ("stage2", chat_stage2, False),             # always runs
        ("caption", caption_model, False),          # always runs, at least once
    ):
        cost, src = stage_cost(name, model, rates, learned, effort)
        parts[name] = {"model": model, "cached": is_cached, "cost": cost, "source": src}
        if cost is None:
            next_total = full_total = None
            source = None
            continue
        if next_total is not None:
            # Stage 1.5 is two calls as of 2026-09-26 -- the meaning (cached per
            # period) and the material (never cached) -- both logged under
            # "stage15", so `cost` is the per-call average. A full run pays two;
            # a cached meaning still pays one.
            calls = 2 if name == "stage15" else 1
            full_total += cost * calls
            if not is_cached:
                next_total += cost * calls
            elif name == "stage15":
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

    return {
        "periodKey": period_key,
        "cacheKnown": cache_known,
        "stages": parts,
        "image": {"model": image_model, "quality": quality, "cost": image, "source": image_source},
        "reasoning": effort,
        "next": None if next_total is None else round(next_total, 6),
        "full": None if full_total is None else round(full_total, 6),
        "source": source,
    }


def _label_for(model_id, rate_row):
    return (rate_row or {}).get("label") or model_id


def catalog(width=1024, height=1024, settings=None):
    """Everything the widget needs, in one JSON-able dict.

    Every available model is listed whether or not it has a rate. `cost` is
    None for anything unpriced or not yet measured, and `costState` says which
    of the two is missing so the UI can tell the user what to do about it
    rather than just showing a blank.
    """
    settings = settings if settings is not None else load_settings()
    provider = provider_of(settings)
    rates = load_rates(provider)
    cached = load_availability(provider) or {}
    learned = load_learned_usage()
    available = cached.get("models") or []
    effort = reasoning_of(settings)
    shortlist_chat = set(rates.get("shortlist", {}).get("chat", []))
    shortlist_image = set(rates.get("shortlist", {}).get("image", []))

    chat_rows, image_rows = [], []
    for model_id in available:
        if _SNAPSHOT_RE.search(model_id):
            continue
        kind = classify(model_id, cached)
        if kind == "chat":
            rate = rates["chat"].get(model_id)
            # Per-stage cost for THIS model whether or not it has ever run:
            # measured tokens for the stage priced at this model's rate. See
            # stage_cost for what "actual" vs "est" mean.
            stages = {}
            run_total, run_source = 0.0, "actual"
            for stage in ("stage1", "stage15", "stage2"):
                cost, src = stage_cost(stage, model_id, rates, learned, effort)
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
                elif rate is not None:
                    # Priced per token with no tokens-per-image to apply yet
                    # (OpenRouter's non-OpenAI token-priced models). One
                    # render measures it.
                    cost_state = "unmeasured"
                else:
                    cost_state = "no-rate"
                label = _label_for(model_id, rate)
                tier = quality.capitalize()
                if provider == "openrouter":
                    from openrouter_image_gen import tier_params  # noqa: PLC0415

                    sent = tier_params(((cached.get("imageModels") or {}).get(model_id) or {}).get("supported"), quality)
                    if sent.get("resolution"):
                        tier = f"{tier} ({sent['resolution']})"
                image_rows.append({
                    "model": model_id,
                    "quality": quality,
                    "label": label if quality == "auto" else f"{label} — {tier}",
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
        cost, source = run_cost(chat_model, image_model, quality, render_size_for(width, height), rates, learned,
                                row.get("reasoning", "default") if provider == "openrouter" else "default")
        presets.append({
            "key": key,
            "label": row.get("label", key.capitalize()),
            "chat": chat_model,
            "image": image_model,
            "quality": quality,
            # OpenRouter only: how hard the chat model thinks. See
            # providers.REASONING_EFFORTS.
            "reasoning": row.get("reasoning", "default"),
            "available": chat_model in available_set and image_model in available_set,
            "runCost": cost,
            "runSource": source,
        })
    # Ordered by cost where known, unpriced last, so the list reads as a ramp
    # even before every tier has been measured.
    presets.sort(key=lambda p: (p["runCost"] is None, p["runCost"] or 0))

    return {
        "nextRun": next_run_cost(width, height, settings=settings),
        "presets": presets,
        "fetchedAt": cached.get("fetchedAt"),
        "stale": cache_is_stale(cached),
        "everFetched": bool(cached),
        "chat": rank(chat_rows, rates["hierarchy"].get("chat", []), "model"),
        "image": rank(image_rows, rates["hierarchy"].get("image", []), "model"),
        "ratesPath": str(PIPELINE_DIR / "model-rates.toml"),
        "provider": provider,
    }


def parse_size(value, default_width, default_height):
    """--size as the panel passes it: the raw backgroundSize setting, which
    is "WIDTHxHEIGHT" or "auto" (the default). "auto" resolves the same way
    astro-arc-generate resolves it, so the prices shown match what a run
    would render. int() on "auto" used to raise after --refresh had already
    fetched the list, so the panel got no JSON and the model list never
    loaded. Anything unreadable keeps the defaults: the size only moves cost
    estimates, never which models are listed."""
    if value == "auto":
        try:
            value = subprocess.run(
                [str(Path(__file__).parent / "astro-arc-detect-resolution")],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip() or "1920x1080"
        except (OSError, subprocess.SubprocessError):
            value = "1920x1080"
    try:
        width, height = (int(x) for x in value.split("x"))
    except ValueError:
        return default_width, default_height
    if width <= 0 or height <= 0:
        return default_width, default_height
    return width, height


def main():
    args = sys.argv[1:]
    # astro-arc-config asks for this when the provider changes. Straight from
    # the TOML, so it works before the new provider's models were ever fetched.
    if args[:1] == ["--preset"] and len(args) > 1:
        row = (load_rates(provider_of(load_settings())).get("presets") or {}).get(args[1]) or {}
        print(json.dumps({k: row.get(k) for k in ("chat", "image", "quality", "reasoning") if row.get(k)}))
        return
    # astro-arc-generate asks for this before deciding whether it can afford a
    # quality bump. Prints a number, or "null" when the cost is genuinely not
    # known — the caller must treat null as "unknown", never as "free".
    if "--refresh" in args:
        try:
            refresh_availability(provider_of(load_settings()))
        except Exception as exc:  # noqa: BLE001 — the widget needs a reason, not a traceback
            print(json.dumps({"ok": False, "error": str(exc)[:300]}))
            sys.exit(1)
    width = height = 1024
    for i, a in enumerate(args):
        if a == "--size" and i + 1 < len(args):
            width, height = parse_size(args[i + 1], width, height)
    result = catalog(width, height)
    result["ok"] = True
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
