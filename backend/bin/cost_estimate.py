#!/usr/bin/env python3
"""Turns API token usage into a dollar estimate, for the per-generation
cost log. Real token counts always come straight from each API response's
own `usage` field — never guessed. Converting those into dollars needs a
per-token rate, and that part IS a guess for anything outside the handful
of models below: OpenAI's per-token rates change, and a model the user
typed into a freeform field (Stage 1/2 models are freeform, not a fixed
dropdown) might not be one this table has ever heard of. Where the rate is
unknown, functions here return a cost of None rather than a fabricated
number — callers show "cost unknown" rather than a false total.

Rates current as of this writing (see openai_image_gen.py's note on the
image rates specifically) — recheck platform.openai.com/docs/pricing
before trusting any of this for real budgeting.
"""

# model -> {input: $/1M tokens, output: $/1M tokens}. Deliberately small:
# only models confidently known at the time this was written. Extend as
# needed rather than guessing at ones not listed.
CHAT_MODEL_RATES = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
}

# Same shape, for the image-generation call — matches openai_image_gen.py's
# MODEL_COSTS rate assumptions (kept here too so both the real-usage path
# below and that module's own quality-tier estimate agree with each other).
IMAGE_MODEL_RATES = {
    "gpt-image-2": {"input": 8.00, "output": 30.00},
    "gpt-image-1-mini": {"input": 2.50, "output": 8.00},
    "gpt-image-1": {"input": 10.00, "output": 40.00},
}


def _rate_cost(rates, input_tokens, output_tokens):
    if not rates:
        return None
    return round(
        (input_tokens or 0) * rates["input"] / 1_000_000
        + (output_tokens or 0) * rates["output"] / 1_000_000,
        6,
    )


def chat_call_cost(model, usage):
    """usage is a chat completion response's `usage` dict
    ({"prompt_tokens":..., "completion_tokens":...}) or None."""
    if not usage:
        return None
    rates = CHAT_MODEL_RATES.get(model)
    return _rate_cost(rates, usage.get("prompt_tokens"), usage.get("completion_tokens"))


def image_call_cost(model, usage):
    """usage is an image-generation response's `usage` dict, if the API
    included one — field names aren't confirmed the same as chat
    completions', so this checks a couple of plausible shapes. Returns
    None (not a guess) if no usage was reported at all; callers fall back
    to the quality-tier estimate in that case."""
    if not usage:
        return None
    rates = IMAGE_MODEL_RATES.get(model)
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    return _rate_cost(rates, input_tokens, output_tokens)
