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

The provider comes from the `provider` setting, read fresh on every call like
everything else in the settings file. ASTRO_ARC_PROVIDER overrides it, which
is what the bakeoff/sweep scripts use to try the other provider without
touching the saved setting.
"""

import json
import os
from pathlib import Path

SETTINGS_PATH = Path.home() / ".local/state/omarchy/settings/astro-arc.json"

PROVIDERS = {
    "openai": {
        "label": "OpenAI",
        "apiBase": "https://api.openai.com/v1",
        # Defaults for a config that has never had a model picked. They match
        # astro-arc-config's DEFAULTS and astro-arc-generate's fallbacks.
        "defaultChat": "gpt-4o-mini",
        "defaultImage": "gpt-image-1-mini",
        "defaultQuality": "medium",
    },
    "openrouter": {
        "label": "OpenRouter",
        "apiBase": "https://openrouter.ai/api/v1",
        "defaultChat": "openai/gpt-4o-mini",
        "defaultImage": "openai/gpt-image-1-mini",
        "defaultQuality": "medium",
    },
}


def current_provider(settings=None):
    override = os.environ.get("ASTRO_ARC_PROVIDER")
    if override in PROVIDERS:
        return override
    if settings is None:
        try:
            settings = json.loads(SETTINGS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            settings = {}
    provider = settings.get("provider") or "openai"
    return provider if provider in PROVIDERS else "openai"


def api_base(provider=None):
    return PROVIDERS[provider or current_provider()]["apiBase"]


def default_for(kind, provider=None):
    """kind is "Chat", "Image" or "Quality"."""
    return PROVIDERS[provider or current_provider()]["default" + kind]


# How hard OpenRouter's thinking models think before answering. Reasoning
# tokens bill at the output rate and never appear in the reply: on the
# 2026-10-24 sweep DeepSeek V4.1 Flash spent 6,850 of them on one Stage 1.5
# call, which made a model listed at a quarter of gpt-4o-mini's price cost
# five times as much per run. "default" sends nothing and leaves it to the
# model; anything else is sent as reasoning.effort.
REASONING_EFFORTS = ("default", "none", "minimal", "low", "medium", "high")


def chat_reasoning(settings=None):
    override = os.environ.get("ASTRO_ARC_REASONING")
    if override in REASONING_EFFORTS:
        return override
    if settings is None:
        try:
            settings = json.loads(SETTINGS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            settings = {}
    value = settings.get("chatReasoning") or "default"
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


def extra_headers(provider=None):
    """OpenRouter's optional attribution headers. Only the app name and the
    project page go out, nothing about the person running it."""
    if (provider or current_provider()) != "openrouter":
        return {}
    return {
        "HTTP-Referer": "https://github.com/Gmacadoches/astro-arc",
        "X-Title": "Astro-Arc",
    }
