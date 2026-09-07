#!/usr/bin/env python3
"""Astro-Arc's two-stage LLM prompt pipeline — replaces the old
symbol_map.py lookup-table approach. Nothing in here builds imagery from a
fixed vocabulary anymore; the tables that remain (registers.toml,
cliches.toml, styles.toml) are tuning constraints on an LLM, not content
sources.

  Stage 1 (interpret): chart data -> psychological reading (Jungian/
    archetypal, no visual language at all).
  Stage 2 (image): reading -> a single dream-logic image prompt + concept
    tags. Stage 2 never sees the raw chart — only Stage 1's meaning — so
    it images the psychology, not the astronomy.

Anti-repetition is concept-level (tags stored per generation in
history.json), not string-level, plus positive pressure via a rotating
material register — see pick_register()/avoid_concepts() below for why.

A one-time "visual signature" derived from the natal chart (cached in
state, regenerated only if the birth data changes) gives the series a
persistent identity across every reading, on top of which each cycle's
register/imagery varies.

Both stages need a real OpenAI API key (see astro-arc-apikey) regardless
of which backend (local SD or OpenAI) ends up rendering the final image —
Stage 1/2 are themselves OpenAI chat completions.

Usage:
    llm_pipeline.py <reading.json> <metadata_out.json>
Prints the final image prompt to stdout (matching symbol_map.py's old
CLI contract, so astro-arc-generate's call site barely changed) and writes
the full metadata (reading, distillation, concept tags, register, ...) to
metadata_out.json, for last-run.json and the review log to pick up.
"""

import hashlib
import json
import random
import re
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from openai_image_gen import ApiKeyError, get_api_key  # noqa: E402
from cost_estimate import chat_call_cost  # noqa: E402

import urllib.error
import urllib.request

PIPELINE_DIR = Path.home() / ".local/share/omarchy/astro-arc/pipeline"
STATE_DIR = Path.home() / ".local/state/omarchy/astro-arc/pipeline"
HISTORY_FILE = STATE_DIR / "history.json"
SIGNATURE_FILE = STATE_DIR / "visual_signature.json"
READINGS_DIR = STATE_DIR / "readings"

HISTORY_MAX = 30
AVOID_WINDOW = 14
COLD_START_THRESHOLD = 10
REGISTER_LOOKBACK_DAYS = 5

CHAT_API_URL = "https://api.openai.com/v1/chat/completions"


class PipelineError(Exception):
    pass


# ---------------------------------------------------------------------------
# Chat completion helper — both stages and the one-time signature call go
# through this. The key is fetched fresh (via get_api_key(), a single
# secret-tool subprocess call) and used only inline in this one header dict;
# nothing here stores it, logs it, or returns it.
# ---------------------------------------------------------------------------

def _chat_json(model, system_prompt, user_prompt, slot, temperature=0.8):
    """`slot` picks which of the three independently-stored API keys
    (astro-arc-apikey's stage1/stage2/image) authenticates this call —
    Stage 1 and Stage 2 use separate keys/models by design, since they're
    different tasks that may call for different accounts."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        CHAT_API_URL,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {get_api_key(slot)}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise PipelineError(f"OpenAI chat API error {exc.code}: {detail}") from None

    content = body["choices"][0]["message"]["content"]
    # Real token usage straight from the response — this is what the cost
    # log is built from, never a guess at how many tokens a prompt "should"
    # take.
    usage = body.get("usage")
    return _parse_json_object(content), usage


def _parse_json_object(text):
    """Models asked for JSON mostly comply, but strip markdown fences and
    fall back to the first {...} block just in case."""
    text = text.strip()
    text = re.sub(r"^```(json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            return json.loads(match.group(0))
        raise PipelineError(f"Model did not return parseable JSON: {text[:200]!r}")


# ---------------------------------------------------------------------------
# Config loading — registers/cliches/style are hand-edited TOML, read fresh
# every run so an edit takes effect on the next generation with no restart.
# ---------------------------------------------------------------------------

def load_registers():
    path = PIPELINE_DIR / "registers.toml"
    if not path.exists():
        return []
    with open(path, "rb") as f:
        return tomllib.load(f).get("registers", [])


def load_cliches():
    path = PIPELINE_DIR / "cliches.toml"
    if not path.exists():
        return []
    with open(path, "rb") as f:
        return tomllib.load(f).get("terms", [])


def load_styles():
    path = PIPELINE_DIR / "styles.toml"
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_style_suffix(style_key):
    """Falls back to 'symbolist' (the one Phase 3 settled on) if the
    configured key doesn't match anything in styles.toml — a stale/typo'd
    key should degrade to the known-good default, not an empty suffix."""
    styles = load_styles()
    preset = styles.get(style_key) or styles.get("symbolist")
    return preset.get("suffix", "") if preset else ""


# ---------------------------------------------------------------------------
# Rolling history (concept-level anti-repetition + register rotation)
# ---------------------------------------------------------------------------

def load_history():
    if not HISTORY_FILE.exists():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def save_history_entry(entry, history):
    history = [entry] + history
    history = history[:HISTORY_MAX]
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, indent=2))
    return history


def avoid_concepts(history, window=AVOID_WINDOW):
    """Deduped concept tags from the most recent `window` generations —
    passed to Stage 2 as territory to avoid. Tags, not raw prompt text: a
    model told not to reuse "nebula" reaches for "swirling gaseous
    expanse"; a model told to avoid the *concept* cosmic/celestial moves
    somewhere genuinely different."""
    tags = []
    for entry in history[:window]:
        for tag in entry.get("conceptTags", []):
            if tag not in tags:
                tags.append(tag)
    return tags


def pick_register(history, registers):
    """Positive pressure toward variety, not just negative avoidance —
    negative constraints (avoid X) let a model drift to a synonym of X;
    being handed an unrelated register to build from actually moves it to
    a different region of image-space.

    Cold start (fewer than COLD_START_THRESHOLD generations ever): cycle
    registers in order so the first ~10 runs force full coverage before
    the history-based rotation (which only has real signal once ~14
    generations exist) takes over.
    """
    if not registers:
        return None
    if len(history) < COLD_START_THRESHOLD:
        return registers[len(history) % len(registers)]

    cutoff = datetime.now(timezone.utc) - timedelta(days=REGISTER_LOOKBACK_DAYS)
    recently_used = set()
    for entry in history:
        ts = _parse_timestamp(entry.get("timestamp"))
        if ts and ts >= cutoff:
            recently_used.add(entry.get("register"))

    available = [r for r in registers if r not in recently_used]
    if available:
        return random.choice(available)

    # Every register was used in the lookback window (a short register
    # list + frequent generation) — fall back to the least-recently-used
    # one rather than force an artificial "avoid everything" prompt.
    last_used_at = {}
    for entry in history:
        r = entry.get("register")
        if r and r not in last_used_at:
            last_used_at[r] = entry.get("timestamp", "")
    return min(registers, key=lambda r: last_used_at.get(r, ""))


def _parse_timestamp(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Chart -> plain factual description for Stage 1 (no interpretation, no
# imagery — just the facts an astrologer would work from)
# ---------------------------------------------------------------------------

def describe_natal(natal):
    sun = natal["planets"]["sun"]
    moon = natal["planets"]["moon"]
    parts = [f"Sun in {sun['sign']}", f"Moon in {moon['sign']}"]
    houses = natal.get("houses")
    if houses:
        parts.append(f"Ascendant in {houses['ascendant']['sign']}")
    parts.append(f"dominant element {natal['dominantElement']}")
    parts.append(f"dominant modality {natal['dominantModality']}")
    return ", ".join(parts)


def describe_arc(arc):
    frequency = arc["frequency"]
    if frequency == "daily":
        line = f"Timeframe: daily. Transiting Moon in {arc['moonSign']}, {arc['moonPhase']} phase."
        t = arc.get("dominantMoonTransit")
        if t:
            retro = " (retrograde)" if t.get("retrograde") else ""
            house = f", transiting the {t['transitingHouse']}th house" if t.get("transitingHouse") else ""
            line += f" The Moon is in {t['aspect']} with the natal {t['natalBody'].title()}{retro}{house}."
        return line

    if frequency == "weekly":
        line = "Timeframe: this week."
        t = arc.get("dominantTransit")
        if t:
            retro = " (retrograde)" if t.get("retrograde") else ""
            house = f", transiting the {t['transitingHouse']}th house" if t.get("transitingHouse") else ""
            line += (f" The dominant transit is {t['transitingBody'].title()}{retro} in {t['aspect']} "
                     f"with the natal {t['natalBody'].title()}{house}.")
        else:
            line += " No major transit is in tight aspect this week."
        return line

    line = f"Timeframe: this month. Transiting Sun in {arc['transitingSun']['sign']}"
    if arc["transitingSun"].get("house"):
        line += f", the {arc['transitingSun']['house']}th house"
    line += "."
    t = arc.get("dominantOuterTransit")
    if t:
        retro = " (retrograde)" if t.get("retrograde") else ""
        line += f" The month's dominant outer-planet transit is {t['transitingBody'].title()}{retro} in {t['aspect']} with the natal {t['natalBody'].title()}."
    return line


# ---------------------------------------------------------------------------
# Stage 0: the natal-derived visual signature, generated once and cached
# ---------------------------------------------------------------------------

SIGNATURE_SYSTEM = """You are establishing a persistent visual identity for a series of paintings depicting one person's psychological life over time, based on their natal astrological chart. Given their natal placements, invent a fixed visual signature for the series: a palette range (described in words, not hex codes), a recurring quality of light, and a compositional habit or tendency. This signature is reused across every painting in the series regardless of subject matter, so it must be abstract enough to apply broadly but specific enough to feel like a consistent hand. Keep it in the painterly, visionary/symbolist tradition (Redon, af Klint, Carrington) — not digital or photographic.

Respond with a JSON object: {"signature": "2 to 3 sentences describing the palette, light, and compositional habit"}"""


def _natal_hash(config):
    key = f"{config.get('birthDate','')}|{config.get('birthTime','')}|{config.get('latitude','')}|{config.get('longitude','')}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def get_visual_signature(natal, config, stage2_model):
    """Cached by a hash of the birth data, so the (single, cheap) API call
    only happens once per natal chart — the whole point is that this
    *doesn't* change cycle to cycle. Returns (signature, cost) — cost is
    None on a cache hit, since no call was actually made.

    Uses Stage 2's model/key, not Stage 1's: this call is explicitly about
    inventing painterly/visual language (palette, light, composition),
    which is exactly what Stage 1's system prompt forbids its model from
    doing at all — Stage 2's imagery skillset is the relevant one here.
    """
    natal_hash = _natal_hash(config)
    if SIGNATURE_FILE.exists():
        try:
            cached = json.loads(SIGNATURE_FILE.read_text())
            if cached.get("natalHash") == natal_hash and cached.get("signature"):
                return cached["signature"], None
        except (json.JSONDecodeError, OSError):
            pass

    result, usage = _chat_json(stage2_model, SIGNATURE_SYSTEM, describe_natal(natal), "stage2", temperature=0.9)
    signature = result.get("signature", "").strip()
    if not signature:
        raise PipelineError("Signature generation returned no signature.")

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SIGNATURE_FILE.write_text(json.dumps({
        "natalHash": natal_hash,
        "signature": signature,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }, indent=2))
    return signature, chat_call_cost(stage2_model, usage)


# ---------------------------------------------------------------------------
# Stage 1: interpretation
# ---------------------------------------------------------------------------

STAGE1_SYSTEM = """You are a psychologically-oriented astrologer working in a Jungian, archetypal frame. You are given one person's natal chart placements and their current transits for a specific timeframe (daily, weekly, or monthly). Produce a reading of their inner life for this timeframe: what is being asked of them, what movement or theme is active, and what part of the psyche is engaged and how.

Rules:
- No visual language whatsoever. No imagery, no metaphor-as-picture, no "like a mountain," no scenes. Meaning only, in plain psychological prose. If you start thinking in images here, whatever generates the picture from your reading will inherit your clichés.
- Read the aspect honestly, don't default to tension. A hard aspect (square, opposition) often does mean friction, pressure, or a confrontation with something difficult — say so plainly when that's what's there. But a soft aspect (trine, sextile) or an easy lunar phase can just as honestly mean ease, integration, pleasure, confidence, or things clicking into place — don't manufacture conflict where the chart doesn't have any. The full emotional range is available: tension and pressure, yes, but also wonder, excitement, joy, peace, tranquility, playfulness, and quiet contentment. Match the reading's tone to what's actually there, not to a habit of always finding the shadow.
- Frame this teleologically, not just diagnostically. Even a reading built on real friction should carry a sense of what it's moving toward — a threshold being crossed, something being made possible — not just what is under strain for its own sake. Let ease, wonder, and quiet confidence be the readier default when the chart supports them; treat difficulty as part of a movement, not an end state to dwell in.
- Interpret the placements in relation to each other, not as a list. Name the central tension or movement, and how the natal disposition colors how this transit is experienced.
- Note the timeframe's narrative position: is this an opening, a peak, a release, a threshold, an arrival, a celebration, a rest, or something else — pick whatever phrase actually fits, don't default to the heavier-sounding options out of habit.

Respond with a JSON object with exactly these keys:
{"reading": "3 to 5 sentences of interpretation", "distillation": "one line distilling the core emotional truth of this reading", "narrativePosition": "a short phrase naming the narrative position, e.g. opening, peak, release, threshold, arrival, celebration, rest"}"""


def stage1_interpret(reading, stage1_model, period_key, config):
    """Cached per period_key, but keyed to the birth data too (see
    _natal_hash): regenerating the same day/week/month's image (e.g. while
    comparing style variants) shouldn't re-run Stage 1, but editing the
    birth date/time/location must invalidate the cache even within the same
    period, since Ascendant and transit-house placements feed straight into
    describe_natal()/describe_arc()'s prompt text below. Mirrors the same
    hash check get_visual_signature already does. Returns (result_dict,
    cost) — cost is None on a cache hit."""
    READINGS_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = READINGS_DIR / f"{period_key}.json"
    natal_hash = _natal_hash(config)
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            if cached.get("reading") and cached.get("distillation") and cached.get("natalHash") == natal_hash:
                return cached, None
        except (json.JSONDecodeError, OSError):
            pass

    natal = reading["natal"]
    arc = reading["arc"]
    user_prompt = f"Natal: {describe_natal(natal)}.\n{describe_arc(arc)}"

    result, usage = _chat_json(stage1_model, STAGE1_SYSTEM, user_prompt, "stage1", temperature=0.7)
    for key in ("reading", "distillation", "narrativePosition"):
        if not result.get(key):
            raise PipelineError(f"Stage 1 response missing '{key}': {result!r}")

    result["generatedAt"] = datetime.now(timezone.utc).isoformat()
    result["natalHash"] = natal_hash
    cache_path.write_text(json.dumps(result, indent=2))
    return result, chat_call_cost(stage1_model, usage)


# ---------------------------------------------------------------------------
# Stage 2: image prompt
# ---------------------------------------------------------------------------

STAGE2_SYSTEM = """You are inventing a single image to communicate a specific psychological meaning to a viewer who knows nothing about astrology. You are given a psychological reading and its one-line distillation — nothing about the astrology itself. Invent imagery that carries that meaning.

Rules:
- Dream logic, not illustration. Be concrete and oddly specific rather than generically mystical. A dream about grief isn't "a house made of the same grey material as the sky" — it's a particular room, a particular object, a wrongness. Specificity is what makes an image land; vagueness makes it decorative.
- Invent the symbol, don't retrieve one. Reaching for a stock symbol (scales for balance, chains for restriction, a door for opportunity) is a failure. The symbol should feel found, like the psyche produced it for this exact situation.
- Let your assigned register decide whether people belong in this image. If the register is about people or the collective ("figures in relation", "crowd / the collective"), lean into that fully — human presence should usually appear. If the register is a material or place (geological, mechanical, aquatic, botanical, textile and fiber, domestic interior, architectural, bodily and anatomical, atmospheric, ritual object), let that material carry the meaning on its own — the object or place is the protagonist, not a placeholder for an absent person. Only break this and put a figure into a material register if the reading's specific content makes a person's presence unmistakably necessary.
- When people do appear, make them participants in something larger, not the stage for a two-person drama. Prefer a figure or group encountering something vast, strange, numinous, or collective over two individuals performing a legible emotional transaction at each other. Two figures visibly in conflict, or one distressed while another looks on, is exactly the kind of stock symbol the rule above forbids — it explains the tension in literal human terms instead of embodying it. When the reading carries real difficulty, reach for genuine Jungian imagery instead: something devouring or self-consuming, a submersion, a storm, a labyrinth, a guardian barring a threshold, a descent — invented fresh for this specific reading, never picked from this list. Plain interpersonal warmth (two figures simply delighting in each other) is a valid image sometimes, but keep it rare — used often it becomes as much a cliché as staged conflict; more often, even a warm reading's people should be caught up in something larger than each other, not simply pleased with one another.
- When the register is specifically the collective — a crowd, a procession, a public gathering — let the many carry cultural or social material (conformity, belonging, being watched, the individual dissolved into or set against the mass), with one figure marked out from the rest doing the specific psychological work.
- One image, one idea. A single legible visual situation, not a collage of symbols. If you find yourself describing more than three distinct elements, cut it down.
- The relationship carries the meaning. What matters is how things — or people — sit in relation to each other: what's above, behind, inside, turned away from what, who is watching whom.
- Emotional register over subject matter, and let the image pull toward something. The light, scale, and spatial pressure of the image should match the feeling of the reading before any symbol does — and even within real difficulty, the image should carry a sense of movement, threshold, or meaning being made, not tension staged for its own sake.

You are given a fixed visual signature for this whole series (a palette range, quality of light, and compositional habit) — honor it, so this reads as the same hand as every other image in the series regardless of subject.

You are also given one material register to draw the image's physical vocabulary from (still in service of the meaning above, not instead of it), and a list of concepts/symbols to avoid because they were used recently — avoid that entire territory, not just the exact words. If a blocklist of specific terms is given, never use those exact words or close synonyms of them.

Respond with a JSON object with exactly these keys:
{"prompt": "the image-generation prompt, 25 to 50 words, describing only the imagery itself — no style or artist references, those are added separately", "conceptTags": ["2 to 3 short tags naming this image's register at an abstract level, e.g. water/submersion, figure amid a vast unknown, crowd with one marked apart, architectural interior, descent/threshold, geological/weight"]}"""


def stage2_image_prompt(stage1_result, visual_signature, register, avoid_tags, cliches, stage2_model):
    user_lines = [
        f"Reading: {stage1_result['reading']}",
        f"Distillation: {stage1_result['distillation']}",
        f"Narrative position: {stage1_result['narrativePosition']}",
        f"Visual signature for this series: {visual_signature}",
    ]
    if register:
        user_lines.append(f"Material register to draw from: {register}")
    if avoid_tags:
        user_lines.append(f"Avoid these concepts/registers (used recently): {', '.join(avoid_tags)}")
    if cliches:
        user_lines.append(f"Never use these exact terms or close synonyms: {', '.join(cliches)}")

    result, usage = _chat_json(stage2_model, STAGE2_SYSTEM, "\n".join(user_lines), "stage2", temperature=0.95)
    if not result.get("prompt"):
        raise PipelineError(f"Stage 2 response missing 'prompt': {result!r}")
    concept_tags = result.get("conceptTags") or []
    return result["prompt"].strip(), concept_tags, chat_call_cost(stage2_model, usage)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build(reading, config):
    stage1_model = config.get("stage1Model") or "gpt-4o-mini"
    stage2_model = config.get("stage2Model") or "gpt-4o-mini"
    style_key = config.get("artStyle") or "symbolist"
    period_key = reading["arc"]["periodKey"]

    visual_signature, signature_cost = get_visual_signature(reading["natal"], config, stage2_model)
    stage1, stage1_cost = stage1_interpret(reading, stage1_model, period_key, config)

    history = load_history()
    registers = load_registers()
    cliches = load_cliches()
    register = pick_register(history, registers)
    avoid = avoid_concepts(history)

    image_prompt, concept_tags, stage2_cost = stage2_image_prompt(
        stage1, visual_signature, register, avoid, cliches, stage2_model
    )

    style_suffix = load_style_suffix(style_key)
    final_prompt = f"{image_prompt}, {style_suffix}" if style_suffix else image_prompt

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "periodKey": period_key,
        "frequency": reading["arc"]["frequency"],
        "prompt": image_prompt,
        "conceptTags": concept_tags,
        "register": register,
    }
    save_history_entry(entry, history)

    metadata = {
        "reading": stage1["reading"],
        "distillation": stage1["distillation"],
        "narrativePosition": stage1["narrativePosition"],
        "visualSignature": visual_signature,
        "register": register,
        "conceptTags": concept_tags,
        "avoidedConcepts": avoid,
        "artStyle": style_key,
        "imagePrompt": image_prompt,
        "finalPrompt": final_prompt,
        # Cost of the two chat calls this run actually made — None means
        # "cached, no call made" (stage1/signature) or "model not in
        # cost_estimate.py's rate table" (unknown, not zero).
        "stage1Cost": stage1_cost,
        "stage2Cost": stage2_cost,
        "signatureCost": signature_cost,
    }
    return final_prompt, metadata


def main():
    if len(sys.argv) != 3:
        print("Usage: llm_pipeline.py <reading.json> <metadata_out.json>", file=sys.stderr)
        sys.exit(1)

    reading = json.loads(Path(sys.argv[1]).read_text())
    if "error" in reading:
        print(f"Cannot build a prompt: {reading['error']}", file=sys.stderr)
        sys.exit(1)

    config_path = Path.home() / ".local/state/omarchy/settings/astro-arc.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}

    try:
        final_prompt, metadata = build(reading, config)
    except (ApiKeyError, PipelineError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    Path(sys.argv[2]).write_text(json.dumps(metadata, indent=2))
    print(final_prompt)


if __name__ == "__main__":
    main()
