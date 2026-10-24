#!/usr/bin/env python3
"""Which AI provider the pipeline talks to, and the few facts that differ
between them. Everything else (prompts, caching, cost bookkeeping) is the same
code for both.

Two providers:

  openai      api.openai.com directly. Model ids are bare ("gpt-4.1").
  openrouter  openrouter.ai, which proxies OpenAI and many others behind one
              key. Model ids carry the vendor ("openai/gpt-4.1",
              "google/gemini-3.1-flash-image"), so a measurement recorded
              under one provider can never be mistaken for the other's.

The chat call is the same OpenAI-shaped /chat/completions request on both.
The image call is not: OpenRouter has its own /images endpoint that takes an
aspect ratio and a per-model set of knobs instead of OpenAI's fixed pixel
sizes. See openrouter_image_gen.py.

Nothing here reads the settings file. The provider is a fact about a run's
config, so the caller hands the config in (provider_of) and passes the answer
down. The config comes from settings.load(), which is also where the
ASTRO_ARC_PROVIDER / ASTRO_ARC_REASONING overrides the bakeoff and sweep
scripts use are applied, and where each provider's default models live
(settings.toml's [providers] tables).
"""

PROVIDERS = {
    "openai": {
        "label": "OpenAI",
        "apiBase": "https://api.openai.com/v1",
        # The keyring account prefix (see astro-arc-apikey).
        "keyAccount": "openai-api-key",
    },
    "openrouter": {
        "label": "OpenRouter",
        "apiBase": "https://openrouter.ai/api/v1",
        "keyAccount": "openrouter-api-key",
    },
}


def provider_of(config):
    """The provider a run of `config` uses: its `provider`, or OpenAI."""
    provider = (config or {}).get("provider")
    return provider if provider in PROVIDERS else "openai"


def label(provider):
    return PROVIDERS[provider]["label"]


def api_base(provider):
    return PROVIDERS[provider]["apiBase"]


# How hard OpenRouter's thinking models think before answering. Reasoning
# tokens bill at the output rate and never appear in the reply: on the
# 2026-10-24 sweep DeepSeek V4.1 Flash spent 6,850 of them on one Stage 1.5
# call, which made a model listed at a quarter of gpt-4o-mini's price cost
# five times as much per run. "default" sends nothing and leaves it to the
# model; anything else is sent as reasoning.effort.
REASONING_EFFORTS = ("default", "none", "minimal", "low", "medium", "high")


def reasoning_of(config):
    """The chatReasoning a run of `config` uses, or "default". Only
    OpenRouter sends it; on OpenAI it is always "default"."""
    if provider_of(config) != "openrouter":
        return "default"
    value = (config or {}).get("chatReasoning")
    return value if value in REASONING_EFFORTS else "default"


def reasoning_for(listed, effort):
    """The `reasoning` request field for one model, or None to send nothing.

    `listed` is the model's entry from OpenRouter's listing. A model that
    doesn't list reasoning never gets the field. One whose reasoning is
    mandatory rejects "none", so it gets the lowest effort it does take.
    An effort the model doesn't offer is moved to the nearest one below it,
    then above it, so "minimal" on a model without it becomes "low"."""
    info = (listed or {}).get("reasoning")
    if effort == "default" or not info:
        return None
    offered = info.get("supported_efforts") or []
    if info.get("mandatory"):
        offered = [e for e in offered if e != "none"]
    ladder = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
    if effort not in ladder:
        return None
    if effort in offered:
        return {"effort": effort}
    i = ladder.index(effort)
    for candidate in ladder[i::-1] + ladder[i + 1:]:
        if candidate in offered:
            return {"effort": candidate}
    return None


def extra_headers(provider):
    """OpenRouter's optional attribution headers. Only the app name and the
    project page go out, nothing about the person running it."""
    if provider != "openrouter":
        return {}
    return {
        "HTTP-Referer": "https://github.com/Gmacadoches/astro-arc",
        "X-Title": "Astro-Arc",
    }
