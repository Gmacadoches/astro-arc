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

# Rates now live in pipeline/model-rates.toml and are loaded through
# model_catalog, so there is ONE place to add a model rather than four that had
# to be kept in agreement by hand (and had already drifted — gpt-image-1 was
# priced here while being offered by no dropdown at all). The tables are still
# exposed under their original names because callers and tests import them.
from model_catalog import load_rates  # noqa: E402


def _rates(kind):
    return load_rates().get(kind, {})


class _LazyRates(dict):
    """Looks like the old module-level dict, but re-reads the TOML on each
    access so a hand edit takes effect without a restart — the same contract
    registers.toml and styles.toml have."""

    def __init__(self, kind):
        super().__init__()
        self._kind = kind

    def get(self, model, default=None):
        return _rates(self._kind).get(model, default)

    def __getitem__(self, model):
        return _rates(self._kind)[model]

    def __contains__(self, model):
        return model in _rates(self._kind)

    def keys(self):
        return _rates(self._kind).keys()

    def items(self):
        return _rates(self._kind).items()


CHAT_MODEL_RATES = _LazyRates("chat")
IMAGE_MODEL_RATES = _LazyRates("image")


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
