#!/usr/bin/env python3
"""Astro-Arc's three-stage LLM prompt pipeline — replaces the old
symbol_map.py lookup-table approach. Nothing in here builds imagery from a
fixed vocabulary anymore; the tables that remain (registers.toml,
cliches.toml, styles.toml) are tuning constraints on an LLM, not content
sources.

  Stage 1 (interpret): chart data -> psychological reading (Jungian/
    archetypal, no visual language at all).
  Stage 1.5 (amplify): reading -> symbolic material — an archetypal
    constellation named in Jung/Neumann's own terms, a set of specific
    ritual/ethnographic objects, and one deliberate intrusion. No visual
    or compositional language either; it supplies material, not a scene.
    Added 2026-09-09 (see CHANGELOG.md) because asking one call to
    *derive* a symbol from a stated abstraction reliably returns that
    abstraction's nearest visual synonym — gears for harmony, roots for
    grounding — which is a cliché by construction. Producing symbolic
    material is a different job from composing a picture, and it needed
    its own stage.
  Stage 2 (image): reading + material -> a dense dream-logic image prompt
    + concept tags. Stage 2 never sees the raw chart — only Stage 1's
    meaning — so it images the psychology, not the astronomy.

Anti-repetition is concept-level (tags stored per generation in
history.json), not string-level, plus positive pressure via a rotating
material register — see pick_register()/avoid_concepts() below for why.

A one-time "visual signature" derived from the natal chart (cached in
state, regenerated only if the birth data changes) gives the series a
persistent identity across every reading, on top of which each cycle's
register/imagery varies.

All three stages need a real OpenAI API key (see astro-arc-apikey)
regardless of which backend (local SD or OpenAI) ends up rendering the
final image — Stages 1, 1.5 and 2 are themselves OpenAI chat completions.
Stage 1 and Stage 1.5 share the `stage1` key slot and model (both are
meaning-work); Stage 2 and the one-time signature call share `stage2`
(both are imagery-work).

Model choice is not incidental here. Stage 1.5's whole job — naming an
archetypal constellation in Neumann's actual vocabulary and producing
museum-specific ritual objects — is beyond gpt-4o-mini, which returns
generic heritage props ("a wooden family crest bearing symbols of
heritage") and wellness-register constellations no matter how explicit the
system prompt is. Measured 2026-09-09 against gpt-4.1 on the same reading;
see CHANGELOG.md. The chat stages together cost roughly $0.009/generation
at gpt-4.1 versus ~$0.0004 at mini, against a ~$0.013 image render — the
quality difference is very large and the cost difference is not.

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
AMPLIFICATIONS_DIR = STATE_DIR / "amplifications"

HISTORY_MAX = 30
# Narrowed from 14 to 8 on 2026-09-09. At 14 this was handing Stage 2 a
# 40-item "avoid all of this" list on every call — a very large negative-
# constraint load on a model that also has to honor two registers, a style
# world, a signature and a set of amplification objects. Anti-repetition
# now has positive pressure from two colliding registers plus per-cycle
# amplification material, so it no longer has to carry variety on its own.
AVOID_WINDOW = 8
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


def load_style_suffix(style_key, has_people=False):
    """Falls back to 'symbolist' (the one Phase 3 settled on) if the
    configured key doesn't match anything in styles.toml — a stale/typo'd
    key should degrade to the known-good default, not an empty suffix.

    Two coexisting schemas (added 2026-09-08 for ghibli's split — see
    CHANGELOG.md): a preset either defines a bare `suffix` (applied to
    every generation regardless of content — the original shape, still
    exactly how every other preset works, unchanged and unmigrated here)
    or `suffix_base` (always applied) + optional `suffix_figures`
    (appended only when has_people is true). The point of the split is
    that a style's figure-rendering instructions stop being dead text on
    every generation that has no figure in it — previously ~88% of
    ghibli's real output.

    No separator is inserted between `suffix_base` and `suffix_figures` —
    straight concatenation — so each preset's `suffix_figures` must carry
    whatever leading punctuation actually joins correctly onto its own
    `suffix_base`'s ending. ghibli's `suffix_base` is a comma-joined
    descriptive list ending with no trailing period (matching this
    project's older bare-`suffix` presets' style), so its
    `suffix_figures` leads with a comma and continues the list. This is
    NOT a universal convention: an earlier interim version of ghibli's
    `suffix_base` was full-sentence prose ending in a period, and a
    leading comma on `suffix_figures` against *that* produced a real bug
    caught while implementing this ("...Kazuo Oga., figures..." — a
    period directly followed by a comma). Check the actual ending every
    time a preset is written or migrated to this schema; don't assume.

    has_people defaults to False so any existing call site that hasn't
    been updated to pass it still gets suffix_base alone, never a
    surprise figure-rendering clause.
    """
    styles = load_styles()
    preset = styles.get(style_key) or styles.get("symbolist")
    if not preset:
        return ""

    if "suffix_base" in preset:
        suffix = preset.get("suffix_base", "")
        if has_people and preset.get("suffix_figures"):
            suffix += preset["suffix_figures"]
        return suffix

    return preset.get("suffix", "")


def load_style_info(style_key):
    """(label, guidance) for the configured style — guidance tells Stage 2
    what kind of imagery this style can actually depict (see styles.toml's
    header comment), so it doesn't invent imagery outside the style's own
    visual vocabulary. Same fallback-to-symbolist behavior as
    load_style_suffix(); guidance is optional per-preset, defaults to ""
    for an older preset that hasn't been given one yet."""
    styles = load_styles()
    preset = styles.get(style_key) or styles.get("symbolist")
    if not preset:
        return "", ""
    return preset.get("label", ""), preset.get("guidance", "")


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


# Registers grouped by what they're actually made of, used only to pick a
# *non-cohering* secondary (see pick_registers). A pairing inside one group
# — botanical + bodily, architectural + domestic interior — collapses back
# into the single-material monoculture the pair exists to break, so the
# secondary is drawn from a different group whenever one is available.
REGISTER_FAMILIES = {
    "botanical": "living",
    "bodily and anatomical": "living",
    "geological": "mineral",
    "architectural": "built",
    "domestic interior": "built",
    "mechanical": "built",
    "aquatic": "fluid",
    "atmospheric": "fluid",
    "textile and fiber": "made",
    "ritual object": "made",
    "figures in relation": "human",
    "crowd / the collective": "human",
}

# Whether a generation contains people stays gated on the PRIMARY register
# only — see CONTEXT.md's convention note. A "human" register arriving as
# the secondary would inject figures into what is supposed to be a
# material-led scene through a side door, which is exactly the failure the
# register-gating convention was written to prevent, so these are never
# eligible as a secondary.
PEOPLE_REGISTERS = {"figures in relation", "crowd / the collective"}


def pick_registers(history, registers):
    """Returns (primary, secondary). The primary keeps the original
    recency-rotation behavior exactly (pick_register below is still the
    implementation); the secondary is a deliberately non-cohering partner
    that must be physically present in the frame.

    Two materials in one frame is the fix for within-image monoculture: one
    register means one material family means one coherent little world, which
    is how `botanical` reliably produced "a garden with plants in it."
    """
    primary = pick_register(history, registers)
    if not primary:
        return None, None

    candidates = [r for r in registers if r != primary and r not in PEOPLE_REGISTERS]
    if not candidates:
        return primary, None

    primary_family = REGISTER_FAMILIES.get(primary)
    distant = [r for r in candidates if REGISTER_FAMILIES.get(r) != primary_family]
    pool = distant or candidates

    # Don't re-run the same pairing two cycles in a row.
    recent_secondaries = {e.get("secondaryRegister") for e in history[:2]}
    fresh = [r for r in pool if r not in recent_secondaries]
    return primary, random.choice(fresh or pool)


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

# Style-neutral on purpose (fixed 2026-09-08, see CHANGELOG.md/CONTEXT.md):
# an earlier version of this prompt named a tradition and specific artists
# ("painterly, visionary/symbolist tradition (Redon, af Klint, Carrington)"),
# which meant every generation carried symbolist-flavored steering language
# regardless of which art style the user actually selected — it fought
# ghibli/cyberpunk/etc.'s own guidance, and content-invention consistently
# lost to it (see the `ghibli` "architectural" register failure, which read
# as symbolist rather than Ghibli). This signature's only job is palette/
# light/contrast/composition continuity across a series — that job needs no
# named tradition, and naming one hijacks style selection instead.
SIGNATURE_SYSTEM = """You are establishing a persistent visual identity for a series of paintings depicting one person's psychological life over time, based on their natal astrological chart. Given their natal placements, invent a fixed visual signature for the series: a dominant palette, a quality and direction of light, a contrast level, and a compositional tendency. This signature is reused across every painting in the series regardless of subject matter *and* regardless of which rendering style is applied to it — so describe only style-neutral visual qualities, never an art movement, tradition, or artist by name, so it can be painted in any style without fighting that style's own visual world.

Cover all four:
- Palette: which colors dominate and in what balance.
- Light: warm or cool, and its quality/direction (soft and diffuse, hard and directional, low and raking, etc).
- Contrast: high-contrast/dramatic vs. low-contrast/gentle.
- Composition: centered vs. off-balance, crowded vs. sparse, a near or distant vantage point.

Respond with a JSON object: {"signature": "2 to 3 sentences covering the palette, light, contrast, and compositional habit — no art movement, tradition, or artist names"}"""

# Bumped whenever SIGNATURE_SYSTEM's actual content changes in a way that
# should invalidate every previously-cached signature (not just this one
# rewrite) — natalHash alone only catches a birth-data edit, not a prompt
# rewrite, so without this a fixed prompt would silently keep serving the
# old cached (tradition-naming) signature forever on any existing install.
SIGNATURE_SCHEMA_VERSION = 2


def _natal_hash(config):
    key = f"{config.get('birthDate','')}|{config.get('birthTime','')}|{config.get('latitude','')}|{config.get('longitude','')}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def get_visual_signature(natal, config, stage2_model):
    """Cached by a hash of the birth data, so the (single, cheap) API call
    only happens once per natal chart — the whole point is that this
    *doesn't* change cycle to cycle. Returns (signature, cost) — cost is
    None on a cache hit, since no call was actually made.

    Also invalidated by SIGNATURE_SCHEMA_VERSION (see above) — a cache
    written under an older prompt version is treated as a miss even if the
    birth data hasn't changed, so a SIGNATURE_SYSTEM rewrite takes effect
    on every install's next generation, not just a fresh one.

    Uses Stage 2's model/key, not Stage 1's: this call is explicitly about
    inventing painterly/visual language (palette, light, composition),
    which is exactly what Stage 1's system prompt forbids its model from
    doing at all — Stage 2's imagery skillset is the relevant one here.
    """
    natal_hash = _natal_hash(config)
    if SIGNATURE_FILE.exists():
        try:
            cached = json.loads(SIGNATURE_FILE.read_text())
            if (cached.get("natalHash") == natal_hash and cached.get("signature")
                    and cached.get("schemaVersion") == SIGNATURE_SCHEMA_VERSION):
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
        "schemaVersion": SIGNATURE_SCHEMA_VERSION,
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
- Do not soften. Where the chart genuinely carries difficulty — grief, dread, constriction, exposure, loss of control, a confrontation with something unwanted — name it plainly and let it stay uncomfortable. A reading that resolves every difficulty into reassurance is exactly as false as one that manufactures conflict, and it is the more likely failure here. Depth psychology is not a wellness practice: the shadow, dissolution, mortification and being at the mercy of something larger are real contents and are not to be phrased as "growth opportunities."
- Frame this teleologically, not just diagnostically. Even a reading built on real friction should carry a sense of what it's moving toward — a threshold being crossed, something being made possible — not just what is under strain for its own sake. Teleological framing means difficulty belongs to a movement; it does not mean difficulty must be made pleasant, and it never licenses an ending that reassures.
- Avoid therapeutic and self-help register entirely. No "fostering," "nurturing," "embracing," "inviting you to," "personal growth," "your journey," "leaning into." Write as an analyst describing what is happening in a psyche, not as an app encouraging a user.
- Do not write in stacked abstract nouns. "A dynamic exploration of emotional authenticity within personal relationships" and "the integration of security with curiosity" are theme labels, not observations — they describe the category a reading belongs to instead of saying what is happening to this person. Name the actual movement in plain words: what presses, what loosens, what is being noticed, what is being refused. The distillation especially must read as a statement about a person, not a topic heading.
- Interpret the placements in relation to each other, not as a list. Name the central tension or movement, and how the natal disposition colors how this transit is experienced.
- Note the timeframe's narrative position: is this an opening, a peak, a release, a threshold, an arrival, a celebration, a rest, or something else — pick whatever phrase actually fits, don't default to the heavier-sounding options out of habit.

Respond with a JSON object with exactly these keys:
{"reading": "3 to 5 sentences of interpretation", "distillation": "one line distilling the core emotional truth of this reading", "narrativePosition": "a short phrase naming the narrative position, e.g. opening, peak, release, threshold, arrival, celebration, rest"}"""


# Same role SIGNATURE_SCHEMA_VERSION plays for the signature cache: a
# STAGE1_SYSTEM rewrite must invalidate readings cached under the old
# wording, or the fix silently does nothing for every period already
# generated. Bumped 2026-09-09 for the tone-floor rules above.
STAGE1_SCHEMA_VERSION = 3


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
            if (cached.get("reading") and cached.get("distillation")
                    and cached.get("natalHash") == natal_hash
                    and cached.get("schemaVersion") == STAGE1_SCHEMA_VERSION):
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
    result["schemaVersion"] = STAGE1_SCHEMA_VERSION
    cache_path.write_text(json.dumps(result, indent=2))
    return result, chat_call_cost(stage1_model, usage)


# ---------------------------------------------------------------------------
# Stage 1.5: amplification — the symbolic material Stage 2 composes from
# ---------------------------------------------------------------------------

# Amplification is Jung's own term for this operation: set the mythological,
# alchemical, ethnographic and folkloric parallels beside a psychic content
# to give it images with more weight than the patient's own words carry.
# That is exactly the step this pipeline was missing.
#
# The firewall here is the same one Stage 1 has and for the same reason: if
# this stage starts composing a scene, Stage 2 inherits its framing and the
# extra call buys nothing. Stage 1 must not think in images; Stage 1.5 must
# not think in *pictures* — it names material, and something else arranges
# it. The prohibited-object list is deliberately explicit because the
# failure mode being fixed (30/30 generations retrieving the single most
# available metaphor) is precisely a pull toward those objects.
STAGE15_SYSTEM = """You are a Jungian analyst performing amplification on a psychological reading. Amplification is Jung's own method: you set beside a psychic situation the mythological, alchemical, ethnographic and folkloric material that shares its structure — not to explain the situation, but to give it images with far more weight than ordinary description carries.

You are given a reading of one person's inner life for a specific timeframe. Produce the symbolic material that belongs to it.

Work in the vocabulary of Jung and especially Erich Neumann:
- From *The Origins and History of Consciousness*: the uroboros (undifferentiated wholeness before an ego exists), the Great Mother in her nourishing and her devouring aspects, the separation of the World Parents (the wrenching birth of consciousness out of unity), the hero's dragon-fight, the night-sea journey through the belly of the monster, captivity and dismemberment, centroversion, the return with the treasure hard to attain.
- From *The Great Mother*: the vessel as the root symbol of the feminine — the body as container, and therefore the bowl, the oven, the cave, the tomb, the loom, the well, the mill, the ship, the granary, the coffin. The elementary character that holds and will not release, against the transformative character that changes what it holds.
- Alchemical operations where they genuinely fit: nigredo (blackening, putrefaction), albedo, rubedo; the sealed vessel, the coniunctio, the prima materia, calcination, dissolution.

Rules:
- Name the constellation honestly, including when it is dark. Devouring, dissolution, dismemberment, petrification, suffocation and being buried alive are real archetypal contents. Do not soften them into "transition" or "growth." Equally, do not import darkness the reading does not have — a reading of genuine ease amplifies to abundance, feast, ripening, the sacred marriage, the found spring.
- Your objects must be **specific things with a use** — the kind a museum labels with a place and a date. A swaddling band. A beehive oven, still warm. A knotted red cord. A threshing floor. A votive eye of hammered tin. An apiary smoker. A lead curse tablet. A mourning brooch woven from hair. An ex-voto silver leg. A bone flute. A sin-eater's plate. A wax anatomical model. A plague doctor's beak stuffed with rue. A scold's bridle. A dowsing rod. A reliquary holding a tooth.
- NEVER return a generic category or a stock prop. A door, a mirror, a key, a candle, a chain, a scale, a mask, a book, a clock, a lantern, a rope, a bridge, a tapestry, a gear, a tree with visible roots — these and anything similarly available are failures. If an object could illustrate any reading whatsoever, it is the wrong object.
- Do not draw every object from one culture, one century, or one material. Reach across traditions and across the material world: bone, wax, lead, cloth, grain, glass, iron, salt.
- **Use no visual or compositional language at all.** Do not say where anything sits, how it is lit, what color it is, what it looks like, or how any two things are arranged relative to each other. You are not staging a picture. If you begin composing, you have failed this task.

Respond with a JSON object with exactly these keys:
{"constellation": "one sentence naming the archetypal situation actually active, in the vocabulary above", "movement": "one short phrase naming what is moving into what — e.g. 'uroboric containment giving way to first separation', 'nigredo, the blackening not yet past'", "objects": ["4 to 7 specific ritual, domestic, or ethnographic objects, each named concretely enough that a curator could find one"], "intrusion": "exactly one further object that belongs to a completely different world from the others — a different century, a different technology, a different order of reality — and that nothing in the rest of the material explains", "affect": "3 to 6 words naming the felt bodily quality, not an emotion label — e.g. 'close, warm, faintly suffocating' or 'dry, ringing, too bright'"}"""

# Bumped whenever STAGE15_SYSTEM changes in a way that should invalidate
# every cached amplification, for the same reason SIGNATURE_SCHEMA_VERSION
# exists.
STAGE15_SCHEMA_VERSION = 1


def stage15_amplify(stage1_result, model, period_key, config, cache=True):
    """Cached per period_key exactly like stage1_interpret() — regenerating
    the same day's image must not re-bill this call — and invalidated by the
    same natalHash plus a schema version.

    Uses Stage 1's model and key slot, not Stage 2's: this is meaning-work,
    not imagery-work. (get_visual_signature deliberately does the opposite,
    for the mirror-image reason — it exists to invent painterly language,
    which is exactly what Stage 1's prompt forbids.)

    `cache=False` is for sweep.py, which needs a fresh amplification per run
    and must never write into the production cache.

    Returns (result_dict, cost) — cost is None on a cache hit.
    """
    natal_hash = _natal_hash(config)
    cache_path = AMPLIFICATIONS_DIR / f"{period_key}.json"
    if cache and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            if (cached.get("constellation") and cached.get("objects")
                    and cached.get("natalHash") == natal_hash
                    and cached.get("schemaVersion") == STAGE15_SCHEMA_VERSION):
                return cached, None
        except (json.JSONDecodeError, OSError):
            pass

    user_prompt = (
        f"Reading: {stage1_result['reading']}\n"
        f"Distillation: {stage1_result['distillation']}\n"
        f"Narrative position: {stage1_result['narrativePosition']}"
    )
    result, usage = _chat_json(model, STAGE15_SYSTEM, user_prompt, "stage1", temperature=0.95)

    for key in ("constellation", "movement", "objects", "intrusion", "affect"):
        if not result.get(key):
            raise PipelineError(f"Stage 1.5 response missing '{key}': {result!r}")
    if not isinstance(result["objects"], list):
        raise PipelineError(f"Stage 1.5 'objects' is not a list: {result['objects']!r}")

    result["generatedAt"] = datetime.now(timezone.utc).isoformat()
    result["natalHash"] = natal_hash
    result["schemaVersion"] = STAGE15_SCHEMA_VERSION
    if cache:
        AMPLIFICATIONS_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(result, indent=2))
    return result, chat_call_cost(model, usage)


# ---------------------------------------------------------------------------
# Stage 2: image prompt
# ---------------------------------------------------------------------------

STAGE2_SYSTEM = """You are composing a single dense image to communicate a specific psychological meaning to a viewer who knows nothing about astrology. You are given a psychological reading, its one-line distillation, and — most importantly — a set of amplification material: an archetypal constellation, a handful of specific ritual objects, and one intrusion. Your job is to BUILD A SCENE OUT OF THAT MATERIAL. You are not inventing a symbol from scratch; the symbols have already been found for you. Compose them.

WHAT THE IMAGE MUST CONTAIN

- Length. The prompt is 90 to 130 words and must never exceed 140. Count them before you answer. Density comes from what you name, not from how long the sentences are.
- Density. Six to ten discrete, nameable things, distributed across three depths: something close to the viewer, a situation in the middle distance, and a far distance that keeps going. A sparse image with one subject on a plain field is the failure this instruction exists to prevent. Do not "simplify for legibility" — an image the eye can finish in one second is the thing being fixed.
- Use most of the amplification objects you were given. Put them in the scene as real physical things, at different depths and different scales. You may add a small number of connective things the scene needs to hold together, but the given objects are the substance.
- THE INTRUSION IS MANDATORY. Place the intrusion object in the scene as a solid, physically present, matter-of-fact thing — the same weight, wear, dirt and lighting as everything else. It must NOT glow, float, shimmer, be translucent, be described as magical or otherworldly, or be visually marked out as special in any way. Nothing in the scene reacts to it. No one looks at it. It is simply there, belonging to another world entirely, and completely unexplained. That unremarked wrongness is where the whole psychological charge of the image comes from. An intrusion that has been made to fit the scene has been destroyed.
- Interaction, not arrangement. Things must be doing something to each other — feeding, binding, eroding, sheltering, crushing, spilling into, growing through, watching. A still-life of symbols placed side by side is a failure. Say what is happening between things, not just what is present.
- Material collision. You are given two material registers. THE AMPLIFICATION OBJECTS ARE WHAT IS IN THE SCENE; THE REGISTERS ARE WHAT THE SCENE IS MADE OF. The primary register supplies the setting and the substance the scene is built from; the secondary must also be physically present as real matter, not as a mood, a color or a passing mention — stone against cloth, machinery against flesh, water against paper. Each register must contribute at least one substantial named thing of its own, beyond the objects you were given. Test yourself: if this scene would read exactly the same with both registers swapped for two others, you have ignored them and must rebuild it. A register named "mechanical" means an actual mechanism with working parts is in the frame; "aquatic" means real water; "architectural" means real built structure. The friction between two unrelated materials in one frame is a large part of what makes the image worth looking at.

HOW TO WRITE IT

- Describe only what is physically there. NEVER explain what anything means. The following words and every close relative of them are forbidden in your output: symbolizing, symbolic, suggesting, suggestive, representing, as if, evoking, conveying, inviting, embodying, reflecting (in the figurative sense), hinting, capturing, "a sense of", "a feeling of", "creating an atmosphere of". An image model cannot paint a verb about meaning; every one of those words spends your budget to produce nothing. Say the thing; let it mean what it means.
- Concrete and oddly specific over generically mystical. Name particular objects, particular wear, particular substances. "A dented milk pail half full of chalk" beats "vessels of nourishment."
- Avoid generic intensifiers. "Vibrant," "intricate," "swirling," "glowing," "mystical," "ethereal," "magical" are close to meaningless to an image model and are this pipeline's known crutch words. Prefer a described fact over an intensifier.
- Match the light, scale and spatial pressure to the reading's actual feeling before any symbol does. When the reading is dark, the image is genuinely dark — close, heavy, dim, cold, airless, or too exposed — not a pleasant scene with a sad object in it. When the reading is light, let it be genuinely light. Do not default to warm golden pleasantness; that is this pipeline's known failure mode.

PEOPLE

- Let the primary register decide whether people belong. If it is "figures in relation" or "crowd / the collective," lean fully into human presence. If it is a material or place register, let the material carry the image — the objects and place are the protagonists — unless the reading's content makes a person unmistakably necessary.
- When people do appear, make them participants in something larger, not a two-person drama. Two figures visibly in conflict, or one distressed while another looks on, explains the tension in literal human terms instead of embodying it — a failure. When the register is specifically the collective, let the many carry cultural or social material, with one figure marked out from the rest.
- FIGURE HIERARCHY IS MANDATORY WHENEVER PEOPLE APPEAR. At most **two** people may have a face turned toward the viewer, and at least one of those must be close to the camera and large in frame. Everyone else is turned away, seen from behind, in profile, bent to a task, occluded by an object, or far enough back to be a silhouette. Never describe a row, line, cluster, or group of people all facing the viewer at the same distance — an image with several equally-sized mid-distance faces is a guaranteed failure, because each face ends up too small to render correctly and the whole group comes out distorted. Say explicitly, in the prompt, who is near and facing, and that the others are turned away or distant.
- People are not a way to reach the density requirement. A crowd counts as ONE element no matter how many bodies are in it. Reach the six-to-ten count with objects, structures and materials, never by multiplying faces.

STYLE

- You are told which rendering style this image will be painted in, along with a description of what it can depict. Treat that description as a source of invention, not just a limit: some styles are entire authored worlds with their own recurring subjects, textures and moods, not a technique you could apply to any subject — reach into that world and invent imagery that belongs there. Where the description is a boundary (what the style cannot render), it still applies as one: a grounded, representational style cannot credibly render a being made of pure energy; a visionary style can. Find a concrete equivalent inside the style's vocabulary that carries the same meaning.
- The registers still name the actual subject. The style's world supplies texture, mood and recurring qualities for rendering that subject — never a replacement for it. A style whose world leans pastoral does not turn every register into a cottage garden: "geological" in that world is still fundamentally stone; "mechanical" is still a mechanism. If you notice yourself reaching for the same handful of style-world subjects regardless of the registers you were given, the registers are being overridden — don't let that happen.
- A grounded style is not an obstacle to the intrusion. Every authored world contains the genuinely strange treated as completely ordinary. Render the intrusion with that world's own physical solidity rather than softening it, and never drop it because it seems not to fit — not fitting is its entire function.

SIGNATURE, REGISTERS, AVOIDANCE

You are given a fixed visual signature for the series. Honor its quality and direction of light, its contrast level, and its compositional habit, so this reads as the same hand as the rest of the series. Do NOT copy its color words into your prompt — naming the same palette every time is what has made this series' images monotonous. Let the light and composition carry the continuity; let each scene's own materials decide its colors.

You are also given concepts to avoid because they were used recently — avoid that whole territory, not just the exact words. If a blocklist of specific terms is given, never use those exact words or close synonyms.

Respond with a JSON object with exactly these keys:
{"prompt": "the image-generation prompt, 90 to 130 words and never more than 140, describing only the imagery itself — no style or artist references, those are added separately", "conceptTags": ["2 to 3 short tags naming this image's register at an abstract level, e.g. water/submersion, figure amid a vast unknown, crowd with one marked apart, architectural interior, descent/threshold, geological/weight"], "hasPeople": "true if the prompt describes any human figure, pair, or crowd — however incidental — false if it's purely objects/places/materials with no person in it"}"""


def stage2_image_prompt(stage1_result, visual_signature, register, avoid_tags, cliches, stage2_model,
                         style_label=None, style_guidance=None, secondary_register=None,
                         amplification=None):
    """`amplification` is Stage 1.5's output (see stage15_amplify). It is
    passed last and defaults to None so the function still works without a
    Stage 1.5 result — sweep.py relies on that to produce a pre-Stage-1.5
    baseline against the same code."""
    user_lines = [
        f"Reading: {stage1_result['reading']}",
        f"Distillation: {stage1_result['distillation']}",
        f"Narrative position: {stage1_result['narrativePosition']}",
    ]

    if amplification:
        objects = amplification.get("objects") or []
        user_lines += [
            "",
            "AMPLIFICATION MATERIAL — compose the scene out of this:",
            f"  Archetypal constellation: {amplification.get('constellation', '')}",
            f"  Movement: {amplification.get('movement', '')}",
            f"  Objects: {'; '.join(objects)}",
            f"  INTRUSION (must appear, physically solid, unexplained, unremarked): {amplification.get('intrusion', '')}",
            f"  Felt quality: {amplification.get('affect', '')}",
            "",
        ]

    user_lines.append(f"Visual signature for this series (honor light/contrast/composition; do NOT copy its color words): {visual_signature}")

    if style_label and style_guidance:
        user_lines.append(f"Rendering style for this image: {style_label} — {style_guidance}")
    if register:
        user_lines.append(f"Primary material register (the scene's protagonist material): {register}")
    if secondary_register:
        user_lines.append(f"Secondary material register (must be physically present in the frame as real matter): {secondary_register}")
    if avoid_tags:
        user_lines.append(f"Avoid these concepts/registers (used recently): {', '.join(avoid_tags)}")
    if cliches:
        user_lines.append(f"Never use these exact terms or close synonyms: {', '.join(cliches)}")

    result, usage = _chat_json(stage2_model, STAGE2_SYSTEM, "\n".join(user_lines), "stage2", temperature=0.95)
    if not result.get("prompt"):
        raise PipelineError(f"Stage 2 response missing 'prompt': {result!r}")
    concept_tags = result.get("conceptTags") or []
    # hasPeople should come back as a real JSON boolean under json_object
    # mode, but coerce defensively in case a model ever emits "true"/"false"
    # as a string instead.
    has_people = result.get("hasPeople")
    if isinstance(has_people, str):
        has_people = has_people.strip().lower() == "true"
    has_people = bool(has_people)
    return result["prompt"].strip(), concept_tags, has_people, chat_call_cost(stage2_model, usage)


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
    amplification, stage15_cost = stage15_amplify(stage1, stage1_model, period_key, config)

    history = load_history()
    registers = load_registers()
    cliches = load_cliches()
    register, secondary_register = pick_registers(history, registers)
    avoid = avoid_concepts(history)
    style_label, style_guidance = load_style_info(style_key)

    image_prompt, concept_tags, has_people, stage2_cost = stage2_image_prompt(
        stage1, visual_signature, register, avoid, cliches, stage2_model,
        style_label, style_guidance,
        secondary_register=secondary_register,
        amplification=amplification,
    )

    # has_people is already known by this point (stage2_image_prompt above
    # returned it) — no call-ordering change needed, just threading the
    # value through so a suffix_base/suffix_figures preset (see
    # load_style_suffix) only pays for figure-rendering instructions on a
    # generation that actually has a figure in it.
    style_suffix = load_style_suffix(style_key, has_people)
    final_prompt = f"{image_prompt}, {style_suffix}" if style_suffix else image_prompt

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "periodKey": period_key,
        "frequency": reading["arc"]["frequency"],
        "prompt": image_prompt,
        "conceptTags": concept_tags,
        # "register" keeps its original meaning (the primary) so
        # pick_register()'s recency scan still reads every pre-2026-09-09
        # history entry correctly; the secondary is a new adjacent key
        # rather than a change of shape.
        "register": register,
        "secondaryRegister": secondary_register,
    }
    save_history_entry(entry, history)

    metadata = {
        "reading": stage1["reading"],
        "distillation": stage1["distillation"],
        "narrativePosition": stage1["narrativePosition"],
        "visualSignature": visual_signature,
        "constellation": amplification.get("constellation"),
        "movement": amplification.get("movement"),
        "amplificationObjects": amplification.get("objects"),
        "intrusion": amplification.get("intrusion"),
        "affect": amplification.get("affect"),
        "register": register,
        "secondaryRegister": secondary_register,
        "conceptTags": concept_tags,
        "hasPeople": has_people,
        "avoidedConcepts": avoid,
        "artStyle": style_key,
        "imagePrompt": image_prompt,
        "finalPrompt": final_prompt,
        # Cost of the chat calls this run actually made — None means
        # "cached, no call made" (stage1/stage1.5/signature) or "model not
        # in cost_estimate.py's rate table" (unknown, not zero).
        "stage1Cost": stage1_cost,
        "stage15Cost": stage15_cost,
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
