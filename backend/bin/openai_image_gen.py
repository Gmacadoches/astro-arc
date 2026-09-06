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
from image_fit import closest_supported_size, fit_cover  # noqa: E402

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
    "gpt-image-2": {
        # gpt-image-2 is used at "auto" quality in this UI, not a fixed
        # tier — this is the estimated range across low..high.
        "auto": round(_GPT_IMAGE_1_TOKENS["high"] * 30 / 1_000_000, 4),
    },
}

PRICE_ESTIMATE_ASSUMPTION = (
    "Estimated from gpt-image-1's published per-quality token counts "
    "applied to this model's own token rate — OpenAI hasn't published "
    "per-tier token counts for this model specifically. Verify at "
    "platform.openai.com/docs/pricing."
)

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
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
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


def check_available_funds(slot="image"):
    """Best-effort — organization cost/usage data requires an Admin API
    key (org-level, created separately in OpenAI's org settings) with the
    api.usage.read scope; a regular project key (the kind stored for
    stage1/stage2/image) gets a 403 here, confirmed empirically against
    /v1/organization/costs, /v1/organization/usage/completions, and the
    old /v1/dashboard/billing/credit_grants (which explicitly refuses any
    secret key, browser-session only). Returns (available, message); if
    available, message carries a short human-readable summary — the exact
    response shape for a successful admin-key call hasn't been observed,
    so this doesn't try to parse a precise dollar figure out of it."""
    try:
        result = _request("/organization/costs", slot=slot)
        return True, f"Organization costs API responded: {json.dumps(result)[:200]}"
    except ApiKeyError as exc:
        return False, str(exc)
    except RuntimeError as exc:
        message = str(exc)
        if "api.usage.read" in message or "403" in message:
            return False, (
                "Not available with this key. Available funds requires an Admin API key "
                "(created in your OpenAI organization's Admin Keys settings, separate from "
                "a regular project key) with the api.usage.read scope."
            )
        return False, f"Funds check failed: {message}"


def generate(prompt, output_path, model="gpt-image-1-mini", quality="medium", target_width=1024, target_height=1024):
    import base64
    import io

    from PIL import Image

    # OpenAI can only render at OPENAI_IMAGE_SIZES, not the arbitrary
    # target the background is actually supposed to end up at — render at
    # the closest-matching aspect ratio, then crop/scale to the exact
    # target below.
    render_size = closest_supported_size(target_width, target_height, OPENAI_IMAGE_SIZES)

    payload = {"model": model, "prompt": prompt, "size": render_size, "n": 1}
    if model != "gpt-image-2":  # gpt-image-2 is used at its own "auto" quality
        payload["quality"] = quality

    result = _request("/images/generations", payload=payload, method="POST", slot="image")
    b64_image = result["data"][0]["b64_json"]
    rendered = Image.open(io.BytesIO(base64.b64decode(b64_image))).convert("RGB")
    fitted = fit_cover(rendered, target_width, target_height)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fitted.save(output_path)

    # Real usage if the API reported one; otherwise fall back to the
    # quality-tier estimate — see cost_estimate.py and MODEL_COSTS' own
    # notes on why both of these are estimates, not confirmed numbers.
    usage = result.get("usage")
    actual_cost = image_call_cost(model, usage)
    quality_key = "auto" if model == "gpt-image-2" else quality
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
    except (ApiKeyError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.cost_out:
        Path(args.cost_out).write_text(json.dumps(cost_info, indent=2))
    print(f"Saved {out}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--validate":
        slot = sys.argv[2] if len(sys.argv) > 2 else "image"
        ok, message = validate_key(slot)
        print(json.dumps({"ok": ok, "message": message}))
        sys.exit(0 if ok else 1)
    if len(sys.argv) > 1 and sys.argv[1] == "--model-choices":
        print(json.dumps(MODEL_CHOICES))
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "--check-funds":
        slot = sys.argv[2] if len(sys.argv) > 2 else "image"
        available, message = check_available_funds(slot)
        print(json.dumps({"available": available, "message": message}))
        sys.exit(0)
    main()
