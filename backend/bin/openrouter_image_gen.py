#!/usr/bin/env python3
"""Image rendering through OpenRouter's /images endpoint. Reached through
openai_image_gen.generate() whenever the provider is "openrouter", so every
caller (astro-arc-generate, sweep.py, the bakeoff) works unchanged.

THE QUALITY TIERS. OpenAI's image API has one knob, `quality`, and this
project's Low/Medium/High presets map straight onto it. OpenRouter fronts
about sixty image models from a dozen vendors and they do not share a knob:

  openai/*            quality = auto|low|medium|high (the 2.5 models add
                      xhigh and max), exactly as on OpenAI directly
  x-ai/grok-imagine-* quality = low|medium, plus resolution = 1K|2K
  google, flux, qwen, resolution = 512|768|1K|1.5K|2K|4K, some subset
  seedream, tencent
  recraft, mai,       neither; one fixed output
  muse-image

GET /images/models says which a model takes (`supported_parameters`), so the
tier is translated per model rather than guessed: `quality` passes through
where it exists, and where it doesn't, low/medium/high become a resolution
step (1K, 2K, the largest offered). A model with neither knob gets the single
tier "auto" and sends nothing extra. Two tiers that would send the identical
request are collapsed into one, so the dropdown never offers a choice that
changes nothing but the label.

SIZE. OpenRouter takes an aspect ratio, not pixels. The closest ratio the
model offers is sent and image_fit.fit_cover() crops to the exact screen
afterwards, the same as the OpenAI path does with its three fixed sizes.

COST. Every response carries usage.cost, the dollars OpenRouter actually
billed. That is recorded as the measured cost for the model/tier/size, so
after one render the catalog shows a real number rather than an estimate.
"""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from http_limits import b64_image_bytes, read_json_capped  # noqa: E402
from image_fit import closest_supported_size, fit_cover  # noqa: E402
from openai_image_gen import (  # noqa: E402
    MODERATION_LEVEL, SAFETY_RETRIES, OPENAI_IMAGE_SIZES, _is_safety_refusal, _request,
)

TIERS = ("low", "medium", "high")

# Resolution tiers in ascending size. Anything a model offers outside this list
# is ignored rather than guessed at.
_RESOLUTION_ORDER = ["512", "768", "1K", "1.5K", "2K", "4K"]

_MEDIA_SUFFIX = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


def _enum(supported, name):
    entry = (supported or {}).get(name) or {}
    return list(entry.get("values") or []) if entry.get("type") == "enum" else []


def _resolution_for(tier, offered):
    """low -> 1K, medium -> 2K, high -> the largest offered, each clamped to
    what the model actually has. 1K is the floor for low because 512 and 768
    upscale visibly on a desktop, and a wallpaper is the one place that shows."""
    offered = [r for r in _RESOLUTION_ORDER if r in offered]
    if not offered:
        return None
    if tier == "high":
        return offered[-1]
    want = "1K" if tier == "low" else "2K"
    at_least = [r for r in offered if _RESOLUTION_ORDER.index(r) >= _RESOLUTION_ORDER.index(want)]
    return at_least[0] if at_least else offered[-1]


def _quality_for(tier, offered):
    if tier in offered:
        return tier
    # Clamp downward: grok offers only low and medium, so its "high" is medium.
    lower = [t for t in TIERS[: TIERS.index(tier)] if t in offered]
    return lower[-1] if lower else None


def tier_params(supported, tier):
    """The extra request fields for one tier on one model. {} when the model
    has no knob for it."""
    params = {}
    quality = _quality_for(tier, _enum(supported, "quality")) if tier in TIERS else None
    if quality:
        params["quality"] = quality
    resolution = _resolution_for(tier, _enum(supported, "resolution")) if tier in TIERS else None
    if resolution:
        params["resolution"] = resolution
    return params


def tiers_for(supported):
    """The tiers worth offering for a model, distinct requests only."""
    seen, tiers = set(), []
    for tier in TIERS:
        params = tier_params(supported, tier)
        key = json.dumps(params, sort_keys=True)
        if params and key not in seen:
            seen.add(key)
            tiers.append(tier)
    return tiers or ["auto"]


def _ratio(value):
    try:
        w, h = (float(x) for x in value.split(":"))
        return w / h
    except (ValueError, ZeroDivisionError):
        return None


def closest_aspect(width, height, offered):
    target = width / height
    best, best_err = None, None
    for value in offered:
        r = _ratio(value)
        if r is None:
            continue
        err = abs(r - target)
        if best_err is None or err < best_err:
            best, best_err = value, err
    return best


def fetch_image_models():
    """GET /images/models plus each model's /endpoints, keyed by id. All public
    and free; no key is sent. The endpoint records carry the pricing and the
    passthrough list, which the listing alone does not."""
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

    listing = _request("/images/models", auth=False).get("data", [])

    def endpoints(model_id):
        try:
            return model_id, _request(f"/images/models/{model_id}/endpoints", auth=False, timeout=20).get("endpoints", [])
        except Exception:  # noqa: BLE001 — a model without pricing is still usable
            return model_id, []

    with ThreadPoolExecutor(max_workers=8) as pool:
        details = dict(pool.map(endpoints, [m["id"] for m in listing]))

    models = {}
    for m in listing:
        eps = details.get(m["id"], [])
        models[m["id"]] = {
            "label": m.get("name") or m["id"],
            "supported": m.get("supported_parameters") or {},
            "passthrough": sorted({
                f"{ep.get('provider_slug')}:{p}"
                for ep in eps for p in ep.get("allowed_passthrough_parameters") or []
            }),
            # The first endpoint is OpenRouter's default route, so its price is
            # the one a request with no routing preference will usually pay.
            "pricing": (eps[0].get("pricing") if eps else None) or [],
        }
    return models


def model_info(model):
    """From the cached catalog when it has the model, else one free lookup."""
    try:
        from model_catalog import load_availability  # noqa: PLC0415

        cached = (load_availability("openrouter") or {}).get("imageModels") or {}
        if model in cached:
            return cached[model]
    except Exception:  # noqa: BLE001
        pass
    try:
        eps = _request(f"/images/models/{model}/endpoints", auth=False, timeout=20).get("endpoints", [])
    except Exception:  # noqa: BLE001 — render anyway with no knobs
        eps = []
    first = eps[0] if eps else {}
    return {
        "supported": first.get("supported_parameters") or {},
        "passthrough": sorted({
            f"{ep.get('provider_slug')}:{p}"
            for ep in eps for p in ep.get("allowed_passthrough_parameters") or []
        }),
        "pricing": first.get("pricing") or [],
    }


def build_payload(prompt, model, quality, target_width, target_height, info):
    supported = info.get("supported") or {}
    payload = {"model": model, "prompt": prompt, "n": 1}
    aspect = closest_aspect(target_width, target_height, [a for a in _enum(supported, "aspect_ratio") if a != "auto"])
    if aspect:
        payload["aspect_ratio"] = aspect
    payload.update(tier_params(supported, quality))
    # The same widened moderation margin the OpenAI path uses (see
    # MODERATION_LEVEL), sent only where OpenRouter lists it as a passthrough
    # for that provider: an unlisted option is a 400 on every render.
    if "openai:moderation" in (info.get("passthrough") or []):
        payload["provider"] = {"options": {"openai": {"moderation": MODERATION_LEVEL}}}
    return payload


def generate(prompt, output_path, model, quality, target_width=1024, target_height=1024):
    info = model_info(model)
    payload = build_payload(prompt, model, quality, target_width, target_height, info)

    for attempt in range(1 + SAFETY_RETRIES):
        try:
            # Image renders on the slower models routinely pass a minute.
            result = _request("/images", payload=payload, method="POST", slot="image", timeout=180)
            break
        except RuntimeError as exc:
            if not _is_safety_refusal(exc) or attempt == SAFETY_RETRIES:
                raise
            print(f"Astro-Arc: image refused by the safety system, retrying "
                  f"({attempt + 1}/{SAFETY_RETRIES})", file=sys.stderr)
            time.sleep(2)

    try:
        item = result["data"][0]
        image_bytes = b64_image_bytes(item["b64_json"])
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("OpenRouter returned no image data.") from None
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = _MEDIA_SUFFIX.get(item.get("media_type") or "", ".png")
    raw_path = output_path.with_name(output_path.stem + ".raw" + suffix)
    raw_path.write_bytes(image_bytes)
    try:
        fit_cover(raw_path, output_path, target_width, target_height)
    finally:
        raw_path.unlink(missing_ok=True)

    usage = result.get("usage") or None
    billed = usage.get("cost") if usage else None
    # Keyed on the same size string the catalog prices under (render_size_for),
    # so the measurement and the dropdown always agree on which row it is.
    render_size = closest_supported_size(target_width, target_height, OPENAI_IMAGE_SIZES)
    try:
        from model_catalog import record_usage  # noqa: PLC0415

        record_usage(model, quality, render_size, usage)
    except Exception:  # noqa: BLE001 — bookkeeping must not lose a paid image
        pass

    return output_path, {
        "cost": billed,
        "costSource": "actual" if billed is not None else None,
        "usage": usage,
    }
