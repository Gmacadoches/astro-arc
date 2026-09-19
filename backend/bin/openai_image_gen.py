#!/usr/bin/env python3
"""Astro-Arc's OpenAI image-generation backend — an alternative to the
local Stable Diffusion pipeline in image_gen.py, for whoever wants higher
quality and is fine paying per image.

Security: the API key is NEVER read from a file, an environment variable,
or a config value in this codebase. It exists only as the return value of
a single `secret-tool lookup` subprocess call, made right where it's used
(request_headers()) and nowhere else. It is never printed, logged, or
written to disk by this script. See astro-arc-apikey for how it's stored
(stdin -> `secret-tool store` directly, never through this script).

Pricing note (as of this writing): OpenAI publishes per-token rates for
these models but not the output-tokens-per-quality-tier each one uses —
that's only documented for the original gpt-image-1 (272/1056/4160 tokens
for low/medium/high at 1024x1024). MODEL_COSTS below applies gpt-image-1's
token counts to the newer models' own per-token rates as an *estimate*,
not a confirmed number — PRICE_ESTIMATE_ASSUMPTION explains this in the
one place the UI needs to quote it. Verify at platform.openai.com/docs/pricing
before relying on this for real budgeting.
"""

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cost_estimate import image_call_cost  # noqa: E402
from http_limits import b64_image_bytes, read_error_capped, read_json_capped  # noqa: E402
from image_fit import ImageError, closest_supported_size, fit_cover  # noqa: E402

API_BASE = "https://api.openai.com/v1"

# The sizes OpenAI's image models actually accept — arbitrary desktop
# resolutions aren't among them, so generate() picks whichever of these is
# closest in aspect ratio, then image_fit.fit_cover() crops/scales the
# result to the exact target size afterward.
OPENAI_IMAGE_SIZES = ["1024x1024", "1024x1536", "1536x1024"]

# gpt-image-1's published output-token counts per quality tier at 1024x1024
# — the only model OpenAI has documented this for. Applied below to other
# models' own $/1M-output-token rate as an estimate.
_GPT_IMAGE_1_TOKENS = {"low": 272, "medium": 1056, "high": 4160}

# model -> quality -> (output $ per 1M tokens, estimated $ per image)
# Rates current as of this writing; recheck platform.openai.com/docs/pricing
# before trusting these for budgeting.
MODEL_COSTS = {
    "gpt-image-1-mini": {
        "low": round(_GPT_IMAGE_1_TOKENS["low"] * 8 / 1_000_000, 4),
        "medium": round(_GPT_IMAGE_1_TOKENS["medium"] * 8 / 1_000_000, 4),
        "high": round(_GPT_IMAGE_1_TOKENS["high"] * 8 / 1_000_000, 4),
    },
    # Legacy estimate table. model_catalog.image_cost() is the real estimator
    # now — it prices any model/tier from the rate table — and this only remains
    # as the fallback inside generate() when the catalog can't be imported.
    # gpt-image-2 is listed per tier rather than only at "auto" since every tier
    # is now selectable on it.
    "gpt-image-2": {
        "low": round(_GPT_IMAGE_1_TOKENS["low"] * 30 / 1_000_000, 4),
        "medium": round(_GPT_IMAGE_1_TOKENS["medium"] * 30 / 1_000_000, 4),
        "high": round(_GPT_IMAGE_1_TOKENS["high"] * 30 / 1_000_000, 4),
        "auto": round(_GPT_IMAGE_1_TOKENS["high"] * 30 / 1_000_000, 4),
    },
}

def quality_key_for(model, quality):
    """The tier spelling to record and to price under.

    This used to force gpt-image-2 to "auto" because the render call refused to
    send a quality for it. Probing the live API on 2026-09-12 showed every image
    model accepts low/medium/high/auto, so that restriction was self-imposed —
    and it meant the most expensive model was pinned to its most expensive
    setting. The requested tier is now both sent and recorded. Kept as a single
    function so the recorder and the estimator can never disagree on the
    spelling, which is a real bug class here even when the mapping is identity.
    """
    return quality


def estimate_cost(model, quality, target_width=1024, target_height=1024):
    """Projected $ for one render at the size it will actually be made at.

    MODEL_COSTS above is per 1024x1024, but nothing here renders at
    1024x1024 — a 1600x900 background renders at 1536x1024 (see
    OPENAI_IMAGE_SIZES / closest_supported_size), which is 1.5x the area and
    costs very close to 1.5x as much. Scaling the documented token counts by
    the real render area matches measured billing closely: for
    gpt-image-1-mini at 1536x1024 this predicts $0.0127 medium / $0.0500
    high against $0.0131 / $0.0504 actually billed across a 12-render sweep
    on 2026-09-09.

    Used by the model catalog to price a tier before it is rendered.
    Still an estimate built on an estimate — see PRICE_ESTIMATE_ASSUMPTION —
    so it returns None rather than a fabricated number for a model/quality
    it doesn't know.
    """
    base = MODEL_COSTS.get(model, {}).get(quality_key_for(model, quality))
    if base is None:
        return None
    render_size = closest_supported_size(target_width, target_height, OPENAI_IMAGE_SIZES)
    w, h = (int(x) for x in render_size.split("x"))
    return round(base * (w * h) / (1024 * 1024), 6)


PRICE_ESTIMATE_ASSUMPTION = (
    "Estimated from gpt-image-1's published per-quality token counts "
    "applied to this model's own token rate — OpenAI hasn't published "
    "per-tier token counts for this model specifically. Verify at "
    "platform.openai.com/docs/pricing."
)

# Models known to accept the /images/generations `moderation` parameter.
#
# Gated by name rather than sent unconditionally because an unrecognized
# parameter is itself a 400: sending this to a model that doesn't take it
# would trade a *rare* moderation block for a *guaranteed* failure on every
# render. A model missing from this set simply gets the default ("auto")
# — the behavior this pipeline had before 2026-09-12.
MODERATION_PARAM_MODELS = {"gpt-image-1-mini", "gpt-image-2"}

# "low" relaxes the safety classifier's threshold; it does NOT disable it.
# Genuinely explicit content still blocks, and a block is still a hard
# failure for the run (see generate()).
#
# Added 2026-09-12 after the day's generation was refused for "sexual"
# content: the "bodily and anatomical" register (registers.toml) had Stage 2
# compose a close-foreground scene of bare anatomy — an outstretched palm,
# fingers, a bent knee, "vertebrae half-wrapped in linen" — which reads to
# the classifier like a partially-draped nude. Nothing in that prompt was
# actually sexual, which is exactly the margin this setting widens. It is
# NOT a fix for the underlying register problem. It widens a margin; it does
# not move a prompt that is genuinely over the line.
#
# CORRECTED 2026-09-13. This comment used to claim the classifier is
# "deterministic per input, so a prompt it refuses is refused identically
# every time" and that "re-rendering the same prompt is always wasted spend".
# Both are false, measured: during verification/people-off-2026-09-13, two
# renders were refused on the first attempt and went through on a later one
# with a byte-identical prompt, model and parameters. The retry loop in
# generate() is earning its keep rather than burning money, and a single
# refusal is not proof that a prompt can never render.
MODERATION_LEVEL = "low"

MODEL_CHOICES = [
    {"model": "gpt-image-2", "quality": "auto", "label": "GPT Image 2", "estCost": MODEL_COSTS["gpt-image-2"]["auto"]},
    {"model": "gpt-image-1-mini", "quality": "low", "label": "GPT Image 1 Mini — Low", "estCost": MODEL_COSTS["gpt-image-1-mini"]["low"]},
    {"model": "gpt-image-1-mini", "quality": "medium", "label": "GPT Image 1 Mini — Medium", "estCost": MODEL_COSTS["gpt-image-1-mini"]["medium"]},
    {"model": "gpt-image-1-mini", "quality": "high", "label": "GPT Image 1 Mini — High", "estCost": MODEL_COSTS["gpt-image-1-mini"]["high"]},
]


class ApiKeyError(Exception):
    pass


# Three independent slots — see astro-arc-apikey's header comment for why
# (stage1/stage2 are the LLM pipeline's chat calls in llm_pipeline.py;
# "image" is this module's own /images/generations call).
_SLOTS = ("stage1", "stage2", "image")


def get_api_key(slot="image"):
    """The one place a key ever exists: the return value of this call.
    Never assign this to a module-level/global variable, never print it,
    never write it anywhere — pass it straight into the request that needs
    it and let it go out of scope."""
    if slot not in _SLOTS:
        raise ValueError(f"Unknown API key slot: {slot!r}")
    # Not on PATH — it's a sibling script in this same bin/ directory, like
    # every other astro-arc-* script this pipeline calls by absolute path.
    apikey_bin = Path(__file__).parent / "astro-arc-apikey"
    try:
        result = subprocess.run(
            [str(apikey_bin), "lookup", slot],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        raise ApiKeyError(f"{apikey_bin} not found.")
    if result.returncode != 0 or not result.stdout.strip():
        raise ApiKeyError(f"No OpenAI API key stored for '{slot}' yet — set one in Astro-Arc's settings.")
    return result.stdout.strip()


def _request(path, payload=None, method="GET", slot="image"):
    """One HTTP call. The Authorization header is built inline from
    get_api_key()'s return value — never stored en route."""
    url = f"{API_BASE}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {get_api_key(slot)}",
            "Content-Type": "application/json",
        },
    )
    try:
        # Bounded read: see http_limits. The 60s timeout stays — it covers a
        # stalled peer, while the cap covers one that keeps talking forever.
        with urllib.request.urlopen(req, timeout=60) as resp:
            return read_json_capped(resp)
    except urllib.error.HTTPError as exc:
        body = read_error_capped(exc)
        # Never include headers (which carried the key) in an error dump.
        raise RuntimeError(f"OpenAI API error {exc.code}: {body[:300]}") from None


def validate_key(slot="image"):
    """The cheap, free validation call the settings UI runs right after
    saving a key: GET /models costs nothing and just confirms the key
    authenticates. Returns (ok, message); message never contains the key."""
    try:
        _request("/models", slot=slot)
        return True, "Key works."
    except ApiKeyError as exc:
        return False, str(exc)
    except RuntimeError as exc:
        message = str(exc)
        if "401" in message:
            return False, "Key was rejected (401 Unauthorized)."
        return False, f"Validation call failed: {message}"


def generate(prompt, output_path, model="gpt-image-1-mini", quality="medium", target_width=1024, target_height=1024):
    # OpenAI can only render at OPENAI_IMAGE_SIZES, not the arbitrary
    # target the background is actually supposed to end up at — render at
    # the closest-matching aspect ratio, then crop/scale to the exact
    # target below.
    render_size = closest_supported_size(target_width, target_height, OPENAI_IMAGE_SIZES)

    payload = {"model": model, "prompt": prompt, "size": render_size, "n": 1, "quality": quality}
    # See MODERATION_LEVEL / MODERATION_PARAM_MODELS above — widens the
    # safety margin for this pipeline's non-sexual anatomical imagery on the
    # models that accept the parameter, and is silently skipped on any that
    # don't rather than risking a 400 on every render.
    if model in MODERATION_PARAM_MODELS:
        payload["moderation"] = MODERATION_LEVEL

    result = _request("/images/generations", payload=payload, method="POST", slot="image")

    # The API's PNG goes to disk as-is, then ImageMagick fits it to the screen.
    # The raw render sits beside the output (same filesystem, so the fit never
    # crosses devices) and is removed whether or not the fit succeeds.
    #
    # Size is checked against MAX_IMAGE_BYTES before the decode allocates and
    # again before anything reaches the disk, so an oversized reply costs
    # neither memory nor a partly written file.
    try:
        image_bytes = b64_image_bytes(result["data"][0]["b64_json"])
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("OpenAI returned no image data.") from None
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path = output_path.with_name(output_path.stem + ".raw.png")
    raw_path.write_bytes(image_bytes)
    try:
        fit_cover(raw_path, output_path, target_width, target_height)
    finally:
        raw_path.unlink(missing_ok=True)

    # Real usage if the API reported one; otherwise fall back to the
    # quality-tier estimate — see cost_estimate.py and MODEL_COSTS' own
    # notes on why both of these are estimates, not confirmed numbers.
    usage = result.get("usage")

    # Measure what this combination really consumed. The old path applied
    # gpt-image-1's published per-tier token counts to other models' rates —
    # an estimate resting on an estimate (see PRICE_ESTIMATE_ASSUMPTION). One
    # real render replaces the token half of that with a measurement, which is
    # also what lets an unpriced model report a real cost as soon as a rate is
    # added to model-rates.toml. Never allowed to break a successful render:
    # the image is already made and paid for by this point.
    try:
        from model_catalog import record_usage  # noqa: PLC0415

        record_usage(model, quality_key_for(model, quality), render_size, usage)
    except Exception:  # noqa: BLE001 — bookkeeping must not lose a paid image
        pass

    actual_cost = image_call_cost(model, usage)
    quality_key = quality_key_for(model, quality)
    estimated_cost = MODEL_COSTS.get(model, {}).get(quality_key)
    cost_info = {
        "cost": actual_cost if actual_cost is not None else estimated_cost,
        "costSource": "actual" if actual_cost is not None else ("estimated" if estimated_cost is not None else None),
        "usage": usage,
    }
    return output_path, cost_info


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Generate one Astro-Arc image via the OpenAI API.")
    parser.add_argument("prompt")
    parser.add_argument("output_path")
    parser.add_argument("--model", default="gpt-image-1-mini")
    parser.add_argument("--quality", default="medium")
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--cost-out", help="write {cost, costSource, usage} JSON here for astro-arc-generate's cost log")
    args = parser.parse_args()

    try:
        out, cost_info = generate(args.prompt, args.output_path, args.model, args.quality, args.width, args.height)
    except (ApiKeyError, RuntimeError, ImageError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.cost_out:
        Path(args.cost_out).write_text(json.dumps(cost_info, indent=2))
    print(f"Saved {out}", file=sys.stderr)


if __name__ == "__main__":
    # astro-arc-generate's cost ceiling asks for this before rendering —
    # keeps the rate table in one place instead of duplicating it in bash.
    if len(sys.argv) > 1 and sys.argv[1] == "--validate":
        slot = sys.argv[2] if len(sys.argv) > 2 else "image"
        ok, message = validate_key(slot)
        print(json.dumps({"ok": ok, "message": message}))
        sys.exit(0 if ok else 1)
    if len(sys.argv) > 1 and sys.argv[1] == "--model-choices":
        print(json.dumps(MODEL_CHOICES))
        sys.exit(0)
    main()
