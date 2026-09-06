#!/usr/bin/env python3
"""Astro-Arc's symbolic vocabulary: turns astro_engine.py's JSON output into
an image-generation prompt.

This is the actual "problem to solve" from the project brief — translating
a reading into imagery that resonates at the right cadence. Everything
below is a first pass, meant to be iterated on once we see real renders:
the vocabulary tables (PLANET_MOTIF, SIGN_LANDSCAPE, ASPECT_COMPOSITION,
MOON_PHASE_MOOD) and STYLE_PREAMBLE are the knobs to turn.

Only the *symbolic content* varies by reading; STYLE_PREAMBLE is fixed so
every generated image reads as the same "deck" rather than a random image
each time — that consistency is the whole point of locking a style before
adding a style picker.

Token budget matters a lot here: CLIP (the text encoder every Stable
Diffusion checkpoint through SDXL uses) silently truncates at 77 tokens —
diffusers only *warns* about it on stderr, it doesn't error, so a
too-long prompt fails quiet and ugly, not loud. A first version of this
file built prompts ~130 tokens long; the truncation was eating the actual
astrology and leaving only the constant style boilerplate, which is
backwards. Every phrase below is kept short for that reason, the
variable "arc" content is assembled first (style is the more expendable,
constant part), and build_prompt() has a hard token check that raises
rather than silently shipping a truncated prompt again.
"""

import json
import sys

# ---- The locked style (v1 — first pass, to be dialed in against real
# renders). Short on purpose — see the token-budget note above. ----------
STYLE_PREAMBLE = "ornate tarot card illustration, art nouveau linework, gold leaf border, jewel-toned, mystical, detailed"

MAX_PROMPT_TOKENS = 77

# ---- Vocabulary tables (kept to a few words each, same reason) ---------

PLANET_MOTIF = {
    "sun": "golden radiant sun",
    "moon": "silver crescent moon",
    "mercury": "quicksilver messenger",
    "venus": "rose and mirror",
    "mars": "flaming blade",
    "jupiter": "crowned oak, halos",
    "saturn": "stone hourglass",
    "uranus": "lightning, shattered geometry",
    "neptune": "misted hidden water",
    "pluto": "shadowed underworld gate",
}

# sign -> (landscape, color tint)
SIGN_LANDSCAPE = {
    "Aries": ("volcanic embers", "crimson"),
    "Taurus": ("a green valley", "emerald"),
    "Gemini": ("twin winds", "pale yellow"),
    "Cancer": ("moonlit tide pools", "silver-blue"),
    "Leo": ("a golden savanna", "gold"),
    "Virgo": ("a ripened wheat field", "earthen brown"),
    "Libra": ("marble scales at dusk", "rose-gold"),
    "Scorpio": ("obsidian depths", "deep crimson"),
    "Sagittarius": ("an open indigo sky", "indigo"),
    "Capricorn": ("granite peaks", "slate grey"),
    "Aquarius": ("starlit currents", "electric violet"),
    "Pisces": ("an oceanic dreamscape", "seafoam violet"),
}

ASPECT_COMPOSITION = {
    "conjunction": "fused as one form",
    "opposition": "mirrored, facing",
    "square": "cut by fracture lines",
    "trine": "joined by a flowing arc",
    "sextile": "linked through a gate",
}

MOON_PHASE_MOOD = {
    "New Moon": "a seed of darkness",
    "Waxing Crescent": "emerging light",
    "First Quarter": "half-lit tension",
    "Waxing Gibbous": "brightening",
    "Full Moon": "full radiance",
    "Waning Gibbous": "receding brilliance",
    "Last Quarter": "half-shadowed release",
    "Waning Crescent": "fading light",
}


class PromptTooLongError(Exception):
    pass


def natal_signature(natal):
    """The fixed 'this is your sky' base motif: natal Sun's motif, sign
    landscape, and color tint — present in every render regardless of
    frequency, so the series has a recognizable identity."""
    sun = natal["planets"]["sun"]
    landscape, tint = SIGN_LANDSCAPE[sun["sign"]]
    return f"{PLANET_MOTIF['sun']}, {landscape}, {tint} tones"


def _transit_suffix(transit):
    """', <composition>, <natal motif>' for a dominant-transit dict, or ''
    if there isn't one. Deliberately doesn't restate the transiting body's
    motif — callers already introduce it themselves (see progression_phrase)
    to avoid saying e.g. "silver crescent moon" twice in one prompt."""
    if not transit:
        return ""
    composition = ASPECT_COMPOSITION.get(transit["aspect"], "near")
    natal_motif = PLANET_MOTIF[transit["natalBody"]]
    retro = " retrograde" if transit.get("retrograde") else ""
    return f"{retro} {composition}, {natal_motif}"


def progression_phrase(arc):
    """The varying 'arc' layer — what actually changes cycle to cycle."""
    frequency = arc["frequency"]

    if frequency == "weekly":
        transit = arc.get("dominantTransit")
        if not transit:
            return "a quiet sky, no strong current"
        motif = PLANET_MOTIF[transit["transitingBody"]]
        return f"{motif}{_transit_suffix(transit)}"

    if frequency == "monthly":
        landscape, tint = SIGN_LANDSCAPE[arc["transitingSun"]["sign"]]
        base = f"the season turning through {landscape}, {tint} light"
        outer = arc.get("dominantOuterTransit")
        if not outer:
            return base
        motif = PLANET_MOTIF[outer["transitingBody"]]
        return f"{base}, {motif}{_transit_suffix(outer)}"

    # daily
    landscape, _tint = SIGN_LANDSCAPE[arc["moonSign"]]
    phase_mood = MOON_PHASE_MOOD.get(arc["moonPhase"], "in motion")
    base = f"{PLANET_MOTIF['moon']}, {phase_mood}, {landscape}"
    return f"{base}{_transit_suffix(arc.get('dominantMoonTransit'))}"


def build_prompt(reading, tokenizer=None):
    """Assemble the final prompt: the variable arc content leads (most
    important, most likely to need the space), the constant style trails
    (expendable — it's the same on every image anyway)."""
    natal = reading["natal"]
    arc = reading["arc"]
    prompt = f"{progression_phrase(arc)}, {natal_signature(natal)}, {STYLE_PREAMBLE}"

    if tokenizer is not None:
        token_count = len(tokenizer(prompt)["input_ids"])
        if token_count > MAX_PROMPT_TOKENS:
            raise PromptTooLongError(
                f"Prompt is {token_count} CLIP tokens, over the {MAX_PROMPT_TOKENS} "
                f"limit — it would be silently truncated: {prompt!r}"
            )
    return prompt


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            reading = json.load(f)
    else:
        reading = json.load(sys.stdin)

    if "error" in reading:
        print(f"Cannot build a prompt: {reading['error']}", file=sys.stderr)
        sys.exit(1)

    try:
        # Best-effort token check from the CLI: only run it if the
        # tokenizer is already cached locally (image_gen.py's model
        # download), so this script stays fast and dependency-light
        # otherwise (astro_engine.py | symbol_map.py needs no torch/HF).
        from pathlib import Path
        from transformers import CLIPTokenizer

        cache_dir = Path.home() / ".local/share/omarchy/astro-arc/models"
        tokenizer = CLIPTokenizer.from_pretrained(
            "stabilityai/sd-turbo", subfolder="tokenizer", cache_dir=str(cache_dir), local_files_only=True
        )
    except Exception:
        tokenizer = None

    print(build_prompt(reading, tokenizer))


if __name__ == "__main__":
    main()
