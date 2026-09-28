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
import time
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from openai_image_gen import ApiKeyError, get_api_key  # noqa: E402
from cost_estimate import chat_call_cost  # noqa: E402
from http_limits import ResponseTooLarge, read_error_capped, read_json_capped  # noqa: E402

import urllib.error
import urllib.request

# Ships beside the code — backend/pipeline, one directory up from backend/bin —
# so the tables travel with the checkout and need no install step.
PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
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

# Bounded retry for the chat API — see _chat_json. 5 attempts with the wait the
# API itself suggests, or doubling from 2s, covers a TPM limit refilling without
# turning a hard failure into a silent 10-minute hang.
CHAT_MAX_ATTEMPTS = 5
CHAT_BACKOFF_BASE = 2.0
CHAT_API_URL = "https://api.openai.com/v1/chat/completions"


class PipelineError(Exception):
    pass


# ---------------------------------------------------------------------------
# Chat completion helper — both stages and the one-time signature call go
# through this. The key is fetched fresh (via get_api_key(), a single
# secret-tool subprocess call) and used only inline in this one header dict;
# nothing here stores it, logs it, or returns it.
# ---------------------------------------------------------------------------

def _chat_json(model, system_prompt, user_prompt, slot, temperature=0.8, stage=None):
    """`slot` picks which of the three independently-stored API keys
    (astro-arc-apikey's stage1/stage2/image) authenticates this call —
    Stage 1 and Stage 2 use separate keys/models by design, since they're
    different tasks that may call for different accounts.

    `stage` names the PIPELINE stage (stage1/stage15/stage2/signature), which is
    not the same thing as `slot` — Stage 1.5 runs on Stage 1's key. It exists
    only to record how many tokens that stage actually consumes, which is what
    lets the widget price a model that has never been run: measured tokens for
    the stage times that model's published rate. See model_catalog.stage_cost.
    """
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }

    # Some models accept only the default temperature and 400 on anything else.
    # Measured: gpt-5-mini, gpt-5.6-luna and gpt-6-astra refuse it; gpt-4.1,
    # gpt-4o-mini and gpt-5.4 accept it. That doesn't follow family lines, so it
    # is discovered rather than tabulated (see model_catalog.get_capability) and
    # remembered, so the wasted attempt happens once per model, not once per call.
    #
    # Dropping temperature is a real change in behavior, not a silent no-op: this
    # pipeline leans on it (0.95 on Stage 2 for imagery variation, 0.7 on Stage 1
    # for a steadier reading). On these models the default is 1, so output runs
    # slightly looser than configured. That beats failing the generation, but it
    # is the reason the widget marks such a model rather than hiding the fact.
    try:
        from model_catalog import get_capability  # noqa: PLC0415

        if get_capability(model, "temperature") is False:
            payload.pop("temperature", None)
    except Exception:  # noqa: BLE001
        pass
    # Retry on 429 and 5xx with backoff (added 2026-09-12). Without this a single
    # rate-limit reply killed the whole generation — and killed it *after* the
    # earlier stages had already been billed, so a 429 on Stage 2 threw away the
    # money spent on Stages 1 and 1.5. It also made sweep.py unusable for the
    # matrices this project relies on for verification: a 12-sample sweep needs
    # roughly 48k tokens against a 30k TPM limit, so it cannot finish inside one
    # minute and MUST pace itself.
    #
    # OpenAI states the wait in the error body ("Please try again in 8.364s"), so
    # honor that when present rather than guessing; otherwise exponential.
    # Deliberately not retrying 4xx other than 429: a bad request or a rejected
    # key will fail identically no matter how long we wait.
    last_detail = None
    # Manual counter rather than `for ... in range(...)` so the temperature
    # fallback below can retry WITHOUT spending one of the backoff attempts —
    # a rejected parameter is not congestion, and a generation that hits both a
    # capability mismatch and a rate limit should still get its full budget of
    # real retries.
    attempt = 0
    while attempt < CHAT_MAX_ATTEMPTS:
        req = urllib.request.Request(
            CHAT_API_URL,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={
                # Rebuilt per attempt so the key is never held across a sleep.
                "Authorization": f"Bearer {get_api_key(slot)}",
                "Content-Type": "application/json",
            },
        )
        try:
            # Bounded read: see http_limits. The timeout covers a stalled
            # peer; the cap covers one that keeps sending.
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = read_json_capped(resp)
            break
        except ResponseTooLarge as exc:
            # Not congestion and not transient: a reply this size is broken,
            # and retrying only invites it again. Fail the stage outright.
            raise PipelineError(f"OpenAI chat API: {exc}") from None
        except urllib.error.HTTPError as exc:
            detail = read_error_capped(exc)[:300]
            last_detail = f"OpenAI chat API error {exc.code}: {detail}"

            # A rejected temperature is a capability mismatch, not congestion:
            # retry immediately without it and remember. Deliberately does NOT
            # consume a backoff attempt, since nothing is overloaded and a 400
            # bills nothing.
            if (exc.code == 400 and "temperature" in detail
                    and "unsupported" in detail.lower() and "temperature" in payload):
                payload.pop("temperature", None)
                try:
                    from model_catalog import set_capability  # noqa: PLC0415

                    set_capability(model, "temperature", False)
                except Exception:  # noqa: BLE001
                    pass
                print(f"llm_pipeline: {model} rejects a custom temperature; retrying at its "
                      f"default and remembering. Output will vary slightly more than configured.",
                      file=sys.stderr)
                continue  # deliberately without incrementing `attempt`

            retriable = exc.code == 429 or 500 <= exc.code < 600
            if not retriable or attempt >= CHAT_MAX_ATTEMPTS - 1:
                raise PipelineError(last_detail) from None
            wait = None
            match = re.search(r"try again in ([0-9.]+)s", detail)
            if match:
                wait = float(match.group(1)) + 1.0
            if wait is None:
                wait = CHAT_BACKOFF_BASE * (2 ** attempt)
            print(f"llm_pipeline: {exc.code} from the chat API, retrying in {wait:.1f}s "
                  f"(attempt {attempt + 1}/{CHAT_MAX_ATTEMPTS})", file=sys.stderr)
            time.sleep(wait)
            attempt += 1
    else:  # pragma: no cover — loop always breaks or raises
        raise PipelineError(last_detail or "OpenAI chat API call failed")

    # Bookkeeping only — never allowed to break a call that has already been paid
    # for, hence the guard and the placement after the response is in hand.
    if stage:
        try:
            from model_catalog import record_stage_usage  # noqa: PLC0415

            record_stage_usage(stage, model, body.get("usage"))
        except Exception:  # noqa: BLE001
            pass

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

def _load_register_families():
    """{family: [register, ...]} straight from registers.toml.

    That file is the only place any of this is written down — see its header.
    Everything below (the flat rotation list, the family lookup) is derived
    from it, so adding a register is one edit in one file and cannot
    half-land. Who appears in the image is actors.toml's job, not this
    file's — see _load_actors()."""
    path = PIPELINE_DIR / "registers.toml"
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            families = tomllib.load(f).get("families", {})
    except (tomllib.TOMLDecodeError, OSError):
        return {}
    return {fam: [r for r in regs if isinstance(r, str) and r.strip()]
            for fam, regs in families.items() if isinstance(regs, list)}


def load_registers():
    """Every register, in file order — which is the order a cold-start install
    cycles through before the recency rotation takes over."""
    return [r for regs in _load_register_families().values() for r in regs]


def register_family(register):
    """Which material family a register belongs to, or None if the file does
    not place it. Two registers with family None would cohere with each other,
    so `None` is a real answer callers have to handle, not a default."""
    for fam, regs in _load_register_families().items():
        if register in regs:
            return fam
    return None


def _load_actors():
    """actors.toml as {"kinds": {...}, "exclude": {...}}, or empty on a
    missing or malformed file."""
    path = PIPELINE_DIR / "actors.toml"
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        print(f"llm_pipeline: actors.toml unreadable ({exc}); no actor will be set",
              file=sys.stderr)
        return {}


def actor_kinds():
    """Every actor kind, in file order — which is the order a cold-start
    install cycles through before the recency rotation takes over, exactly as
    the registers do."""
    return list((_load_actors().get("kinds") or {}).keys())


def actor_kind_summary(kind):
    """The first sentence of a kind's guidance — enough for Stage 1.5 to
    CHOOSE between kinds, without spending the whole paragraph Stage 2 needs
    in order to RENDER one.

    Stage 1.5 used to receive a bare list of names. `solitary` reads as itself
    and `presence` does not, so a spirit with a body and a trade was
    consistently typed `solitary`, and the guidance attached to the kind it
    should have had never fired. The descriptions already existed in
    actors.toml and only the stage that renders could see them.
    """
    text = actor_guidance(kind)
    if not text:
        return ""
    head = text.split(". ")[0].strip().rstrip(".")
    return head


def actor_guidance(kind):
    """How this kind is framed, or "" for `none` and for an unknown kind.

    `none` is a real kind with an empty body: a purely material image is a
    legitimate outcome, just no longer the default it was on ~88% of
    generations.
    """
    return ((_load_actors().get("kinds") or {}).get(kind) or "").strip()


def presence_allowed(style_key):
    """Whether the `presence` actor kind is eligible in this world.

    It shipped ineligible everywhere because most styles forbid the
    immaterial outright and the prohibitions it needed to read lived in
    `guidance`, which the charters replace. So it becomes what the actors
    brief proposed: a question each charter answers. A world that describes
    how the numinous takes a body can have one; a world that says nothing
    cannot, because silence is not permission.
    """
    world = (load_styles().get(style_key) or {}).get("world") or {}
    return bool(isinstance(world, dict) and str(world.get("numinous") or "").strip())


def eligible_actor_kinds(style_key):
    """The kinds Stage 1.5 may choose from in this world."""
    kinds = actor_kinds()
    if not presence_allowed(style_key):
        kinds = [k for k in kinds if k != "presence"]
    return kinds


def actor_excluded_for(register):
    """Actor kinds that collapse into the substance when paired with this
    register — `automaton` with `mechanical` makes the actor out of the same
    material as the world. From actors.toml's [exclude] table, so the pairing
    rule lives beside the kinds rather than in code."""
    return set((_load_actors().get("exclude") or {}).get(register, []))


def load_register_guidance(register):
    """Optional per-register steer from registers.toml's [guidance] table —
    "" for the registers that have none, which is most of them by design.

    Mirrors load_style_info()'s guidance for art styles, and exists for the
    same narrow reason: a register whose obvious reading is much smaller than
    its real territory has to be told so. `man-beast` is the case that
    prompted it — the word lands on minotaurs and satyrs and stops, when the
    register is meant to span every part-human figure in world myth.

    Deliberately NOT a vocabulary list per register. See registers.toml's
    [guidance] header for why that trade goes the way it does.
    """
    if not register:
        return ""
    path = PIPELINE_DIR / "registers.toml"
    if not path.exists():
        return ""
    try:
        with open(path, "rb") as f:
            return tomllib.load(f).get("guidance", {}).get(register, "")
    except (tomllib.TOMLDecodeError, OSError):
        return ""


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


# The order the charter's fields are read out to Stage 2. Fixed here rather
# than taken from the file so every style presents its world the same way, and
# so adding a field to one charter does not silently change another's shape.
_CHARTER_FIELDS = (
    ("era", "Era and technology"),
    ("materials", "What things are made of"),
    ("physics", "How this world behaves"),
    ("society", "Who lives here and how"),
    ("weather", "Weather and light as presence"),
    ("inhabitants", "Who or what acts in this world"),
    ("strangeness", "How the strange is treated"),
    ("numinous", "How the numinous appears, and whether it has a body"),
    ("dread", "How this world goes dark"),
    ("favoured_actors", "Who this world is richest in"),
    ("forbids", "Never in this world"),
)


class _BriefValues(dict):
    """format_map source for brief rules: the dial's values by key, with an
    unknown or missing key left visible as "{key}" rather than raising, so a
    typo in styles.toml shows up in the prompt instead of crashing a run."""
    def __init__(self, dial):
        super().__init__({k: v for k, v in (dial or {}).items() if isinstance(v, str)})

    def __missing__(self, key):
        return "{" + key + "}"


# The brief rules every style ran on until 2026-09-26, verbatim, with the dial
# values as {placeholders}. They were written for ghibli and now live in
# [ghibli.brief]; this copy is only the fallback for styles that have not been
# chartered yet (symbolist, engraving, cosmic, ukiyoe, illuminated), so their
# output does not change until someone decides what theirs should be -- the
# same migration strategy as `guidance` standing in for a missing charter.
DEFAULT_STAGE2_BRIEF = (
    "THE IMPOSSIBLE — applies to every image: at least one thing in frame could not exist, "
    "and nothing in the scene treats it as remarkable. Not a decorated version of an ordinary "
    "thing: a machine that is clearly alive or awake, a creature far too large for its kind, a "
    "structure that walks or floats or grew where it stands, a being that is plainly not a "
    "person. Build it from this world's own materials, give it real weight and wear, light it "
    "like everything else, and let the actor work around it the way they would a wall. An image "
    "in which everything could exist is a failed response.\n"
    "    How much of the frame it takes: {wonder}\n"
    "    What temper it is in: {wonderTone}\n"
    "    SCALE MUST READ. Put something of known human size next to it — a figure, a doorway, a "
    "cart, a gate — close enough in frame to measure it against. A huge thing photographed alone "
    "is just a texture; it only becomes huge beside something that is not.",
    "OPENNESS — applies to every image, whatever the numbers above say: at least half the frame must be open. Sky, horizon, landscape, sea, or a distance that keeps going. Open means depth, not daylight: a night sky, a drop into dark or lit depths, or fog that keeps going all count, and how bright the open part is comes from the light line below, not from this rule. Pull the viewpoint back far enough that this is true. If the scene is indoors, the room must open onto that distance through a large window, a doorway, a missing wall or a broken roof, and what is seen through it must occupy a substantial part of the frame rather than being a bright rectangle in a corner. A fully enclosed interior is a failed response, and so is a close-up that crops the world out.",
)


def load_style_brief(style_key):
    """The rules Stage 2's brief carries for this style, in file order, or
    None for a style with no `[<style>.brief]` table -- which stage2_image_prompt
    reads as "use DEFAULT_STAGE2_BRIEF". An empty table is a real answer (no
    style-specific rules) and returns [], not None.

    Rule text may use the dial's string values as {placeholders} ({wonder},
    {wonderTone}, {light}, ...)."""
    brief = (load_styles().get(style_key) or {}).get("brief")
    if not isinstance(brief, dict):
        return None
    return [str(v).strip() for v in brief.values() if str(v).strip()]


def load_style_world(style_key):
    """The `[<style>.world]` charter as a block of prose, or "" if this style
    has not been chartered yet.

    Charters replace the old free-text `guidance` field, which had to carry a
    world, a set of prohibitions and a plea not to default to a cottage kitchen
    all in one paragraph — and only ghibli ever got a good one. The fields are
    fixed so that every style answers the same questions.

    Returns "" rather than raising for an unchartered style, which is what
    makes the migration incremental: load_style_info() falls back to that
    style's `guidance` until its charter is approved.
    """
    styles = load_styles()
    world = (styles.get(style_key) or {}).get("world") or {}
    if not isinstance(world, dict):
        return ""
    parts = [f"{label}: {str(world[key]).strip()}"
             for key, label in _CHARTER_FIELDS
             if world.get(key) and str(world[key]).strip()]
    return "\n".join(parts)


def load_style_info(style_key):
    """(label, world) for the configured style — what Stage 2 is told about
    the world it is painting in, so it does not invent imagery the style has
    no vocabulary for and can take content from the style rather than only
    rendering.

    The charter wins where one exists; otherwise the old free-text `guidance`
    stands in. That fallback is the whole migration strategy: charters are
    authored and approved one style at a time, and an unchartered style keeps
    working exactly as it did. Same fallback-to-symbolist behaviour as
    load_style_suffix() for an unknown key.
    """
    styles = load_styles()
    preset = styles.get(style_key) or styles.get("symbolist")
    if not preset:
        return "", ""
    key = style_key if style_key in styles else "symbolist"
    return preset.get("label", ""), load_style_world(key) or preset.get("guidance", "")


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


# Same idea as avoid_concepts, one stage earlier, and added for a measured
# reason. preflight_actors.py over this install's first 12 days found Stage 1.5
# reaching for "the separation of the World Parents" on 10 of them (83%), and
# for only 2 distinct archetypes in total, while Stage 1's readings underneath
# varied across the full emotional range and the dial's tonalDirection
# demonstrably steered. STAGE15_SYSTEM offers roughly fifteen archetypes; two
# were ever used.
#
# That is this project's most-repeated failure mode — a model handed an
# abstraction retrieves its nearest synonym — recurring at Stage 1.5. The
# registers solved the same problem with recency pressure rather than a bigger
# vocabulary, so this reuses that, reading the amplification cache because the
# constellation is not recorded in history.json.
AMPLIFY_AVOID_WINDOW = 8


# How many of the immediately preceding days block a repeat of the same actor
# kind. Two, matching the `none` rule (never on consecutive days), and kept
# small deliberately: a large veto window stops being a tie-breaker and starts
# being the chooser, which is arm B wearing arm C's clothes.
# Four, measured. At 2 the constrained arm cycled among four favourites (4
# distinct kinds over 12 days, top share 33%); at 4 it reached 7 distinct and
# 25%, matching the unconstrained random arm's variety while staying coherent.
ACTOR_VETO_WINDOW = 4


def veto_overused_kind(proposed, recent_kinds, kinds):
    """Arm C of the B6 test: Stage 1.5 proposes, recency vetoes afterwards.

    NOT the production path, and kept only so replay_amplify.py can still
    reproduce that arm. It lost for a reason worth remembering: a post-hoc
    veto changes the kind and leaves the actor DESCRIPTION contradicting it,
    which the replay showed plainly — "swarm" attached to "a child resting in
    the crook of a woman's arm". Production constrains the kind up front
    instead (see avoid_kinds), so one call writes both fields and they cannot
    desync.

    The proposal stands unless it repeats one of the last ACTOR_VETO_WINDOW
    kinds, in which case the least-recently-used eligible kind replaces it.
    `none` is never substituted IN — a purely material image has to be chosen
    on the constellation's merits, never as a rotation artifact — but a
    proposed `none` is left alone, since its own recency rule is separate.

    Deterministic, so a replay is reproducible: where several kinds are
    equally stale, file order breaks the tie.
    """
    if not proposed or not kinds:
        return proposed
    blocked = set(recent_kinds[:ACTOR_VETO_WINDOW])
    if proposed not in blocked:
        return proposed
    staleness = {k: recent_kinds.index(k) if k in recent_kinds else len(recent_kinds)
                 for k in kinds}
    eligible = [k for k in kinds if k not in blocked and k != "none"]
    if not eligible:
        return proposed
    return max(eligible, key=lambda k: (staleness[k], -kinds.index(k)))


def recent_constellations(window=AMPLIFY_AVOID_WINDOW, exclude=None):
    """The last `window` constellations, most recent first, for Stage 1.5 to
    reach past. `exclude` drops one period key — the one being regenerated,
    which must not count as its own precedent."""
    if not AMPLIFICATIONS_DIR.is_dir():
        return []
    out = []
    for path in sorted(AMPLIFICATIONS_DIR.glob("*.json"), reverse=True):
        if exclude and path.stem == exclude:
            continue
        try:
            text = (json.loads(path.read_text()).get("constellation") or "").strip()
        except (json.JSONDecodeError, OSError):
            continue
        if text and text not in out:
            out.append(text)
        if len(out) >= window:
            break
    return out


def recent_actor_kinds(window=ACTOR_VETO_WINDOW, exclude=None):
    """The last `window` actor kinds, most recent first, for Stage 1.5 to pick
    around. Same source and same shape as recent_constellations, because it is
    the same mechanism applied to the other field the model over-uses.

    `none` is skipped: it has its own recency rule (never two days running, at
    most twice in seven) and blocking a kind that is meant to be rare would
    invert that.
    """
    if not AMPLIFICATIONS_DIR.is_dir():
        return []
    out = []
    for path in sorted(AMPLIFICATIONS_DIR.glob("*.json"), reverse=True):
        if exclude and path.stem == exclude:
            continue
        try:
            kind = (json.loads(path.read_text()).get("actorKind") or "").strip()
        except (json.JSONDecodeError, OSError):
            continue
        if kind and kind != "none" and kind not in out:
            out.append(kind)
        if len(out) >= window:
            break
    return out


# Registers grouped by what they're actually made of, used only to pick a
# *non-cohering* secondary (see pick_registers). A pairing inside one group
# — botanical + bodily, architectural + domestic interior — collapses back
# into the single-material monoculture the pair exists to break, so the
# secondary is drawn from a different group whenever one is available.
# The one special family. Registers in it are what put people in an image, they
# are gated on the PRIMARY register alone, and they are never eligible as a
# secondary — a human register arriving as the secondary would inject figures
# into a material-led scene through a side door. Named here rather than
# hardcoded in three places; membership lives in registers.toml.
# Backwards-compatible module-level views of registers.toml, for call sites
# (sweep.py, this file) that read them as plain data. Computed at import, which
# is fine because every generation is a fresh process — the file is still read
# fresh on every run.
REGISTER_FAMILIES = {r: fam for fam, regs in _load_register_families().items() for r in regs}

# Kept as an empty set, deliberately, rather than deleted. sweep.py reads it
# through getattr with a default, but an older checkout of any caller that
# reads it directly should get "no register puts people in the image" — which
# is now true — instead of an AttributeError. The people gate is gone; who
# appears is actors.toml's business. Remove once nothing references it.
PEOPLE_REGISTERS = frozenset()

if not REGISTER_FAMILIES:
    print("llm_pipeline: warning — registers.toml has no [families] table; "
          "the register rotation will be empty", file=sys.stderr)


def pick_registers(history, registers, cohesion="collide"):
    """Returns (primary, secondary). The primary keeps the original
    recency-rotation behavior exactly (pick_register below is still the
    implementation).

    Two materials in one frame was the fix for within-image monoculture: one
    register means one material family means one coherent little world, which
    is how `botanical` reliably produced "a garden with plants in it."

    `cohesion` (2026-09-12) decides whether the secondary is a deliberately
    NON-cohering partner, as it always used to be, or a same-family one. Forcing
    collision every single day was one of the four mandates that gave every image
    the same implicit meaning regardless of the chart; on a harmonious day the two
    registers should read as one continuous world instead.

    Note the monoculture risk that motivated collision was measured BEFORE Stage
    1.5 existed, when Stage 2 had to invent its own content. With 4-7 curator-grade
    objects in hand, a single material family no longer means an empty scene — but
    this is the single most important thing to check in a sweep, because if it does
    regress, it regresses toward the exact output this project already rejected.
    """
    primary = pick_register(history, registers)
    if not primary:
        return None, None

    candidates = [r for r in registers if r != primary and r not in PEOPLE_REGISTERS]
    if not candidates:
        return primary, None

    primary_family = REGISTER_FAMILIES.get(primary)
    if cohesion == "cohere":
        # Same family, so the frame reads as one material world. Falling through
        # to None is a legitimate outcome, which the prompt already handles by
        # omitting the secondary line entirely (pre-1.5 history entries have a
        # null secondary). It used to happen to `mineral`, which held only
        # `geological` until the 2026-09-13 expansion gave it two partners; it
        # now happens only to the human registers, which are never eligible as a
        # secondary at all and so can never find a same-family one.
        near = [r for r in candidates if REGISTER_FAMILIES.get(r) == primary_family]
        if not near:
            return primary, None
        pool = near
    else:
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

Cover all three:
- Palette: which colors dominate and in what balance.
- Light: warm or cool, and its quality (soft and diffuse, hard and directional, low and raking, etc).
- Contrast: high-contrast/dramatic vs. low-contrast/gentle.

Do NOT describe composition, density, or vantage point. Say nothing about whether images are crowded or sparse, centered or off-balance, near or distant, busy or still. Those vary per image by design and are decided elsewhere; a fixed compositional habit here would override that variation on every single image in the series.

Respond with a JSON object: {"signature": "2 to 3 sentences covering the palette, light and contrast only — no composition or density, and no art movement, tradition, or artist names"}"""

# Bumped whenever SIGNATURE_SYSTEM's actual content changes in a way that
# should invalidate every previously-cached signature (not just this one
# rewrite) — natalHash alone only catches a birth-data edit, not a prompt
# rewrite, so without this a fixed prompt would silently keep serving the
# old cached (tradition-naming) signature forever on any existing install.
# Bumped 2→3 on 2026-09-12: the signature used to fix a "compositional tendency"
# for the whole series, and the one this install actually generated asked for
# "composition tends to be slightly off-balance and crowded, drawing the viewer
# into a close, intimate exploration." That was injected into EVERY Stage 2 call,
# forever, independently of the density mandate — a second, invisible source of
# the crowded look that would have silently overridden the per-day dial. The
# signature now covers palette, light and contrast only; composition belongs to
# the dial, which varies it per image.
SIGNATURE_SCHEMA_VERSION = 3


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

    result, usage = _chat_json(stage2_model, SIGNATURE_SYSTEM, describe_natal(natal), "stage2", temperature=0.9, stage="signature")
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

    result, usage = _chat_json(stage1_model, STAGE1_SYSTEM, user_prompt, "stage1", temperature=0.7, stage="stage1")
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
# ---- The dial: texture -> imperatives -----------------------------------
#
# Added 2026-09-12. astro_engine.compute_texture() measures the day on four
# independent axes; this is where those numbers become the instructions Stage 1.5
# and Stage 2 actually receive. The split is deliberate and load-bearing in two
# ways.
#
# First, CONTEXT.md's "Stage 2 never receives the raw chart" convention. The
# mapping from number to imperative happens here, in Python, so Stage 2 is handed
# "one site, three named things, 60-80 words" and never an aspect, an orb, or a
# planet. The convention survives in spirit, not just in letter.
#
# Second, these must be COUNTS AND IMPERATIVES, never description. The mandates
# this replaces worked precisely because they were numeric: "six to ten discrete,
# nameable things" is obeyed, while "today is a calm day" loses to the model's
# trained habits every time. Never put a mood word here where a number will do.
#
# Why a dial at all: every generation used to get the same four mandates at full
# strength — 6-10 things across three depths, two colliding registers, one
# unexplained intrusion, a verb list skewed to pressure — so every image carried
# the same implicit meaning (chaos, tension among many unrelated things) whatever
# the chart said. 12 of 13 prompts opened with a near-viewer depth marker. The
# astrology modulated vocabulary and never shape.

# Word budget SHRINKS with density, and this is the single easiest thing here to
# get wrong. A three-object scene written to the old 90-130 word budget leaves the
# model ~60 spare words, and its training spends them on "a sense of" / "inviting
# exploration" — which is exactly the unrenderable interpretive clause that was
# measured in 25 of 30 prompts and that the forbidden-word list exists to kill.
# Fewer things means a shorter prompt, not a wordier one about fewer things.
#
# multiplicity -> (sites, stage15 object range, stage2 thing range, word range)
# Object counts reduced by one at every tier but the lowest, 2026-09-20, when
# the actor took a slot. The actor REPLACES an object rather than joining them:
# fewer objects with one of them being someone is the whole thesis, and adding
# a body on top of an unchanged object count would have made the strewn-objects
# problem worse rather than better.
#
# The lowest tier is left alone deliberately. Dropping it to (1, 2) leaves
# Stage 2 one or two objects plus an actor against a 3-4 thing count, and the
# shortfall would be padded with the "connective things" the prompt permits —
# which is unnamed filler, exactly what a low-density day is supposed to avoid.
#
# Measured before and after on the same hard/hi/3/lo cell: prompts came back at
# 122-127 words against a 90-115 range with the actor added and the old counts,
# i.e. the actor's words were being paid for on top of the objects instead of
# instead of them.
_DENSITY_BY_MULTIPLICITY = (
    # (max_hits, sites, objects,  things,  words)
    (1,         1,     (2, 3),    (3, 4),  (60, 80)),
    (2,         1,     (2, 3),    (4, 5),  (75, 95)),
    (3,         2,     (3, 4),    (5, 7),  (90, 115)),
    (99,        3,     (4, 6),    (6, 10), (100, 130)),
)

# Verbs decide which relations get depicted, so this is shape, not flavor. The old
# list was six pressure verbs plus two neutral ones, applied unconditionally.
_VERBS_HARD = "feeding, binding, eroding, crushing, spilling into, bearing down on"
_VERBS_SOFT = "holding, drying, ripening, settling, leaning against, warming, being mended, being carried"

# How close the event sits — never whether the frame is open. The "hi" cell used
# to read "short sightlines, no far distance to escape into", which is a direct
# instruction to build an enclosed image, and on a high-intensity day it beat
# every other rule because the brief is the part that gets obeyed. Intensity now
# moves where the pressure sits INSIDE an open frame: close and pressing, or far
# and peripheral, with the distance visible either way. See the OPENNESS line in
# the composition brief for why that is invariant.
_SPATIAL_BY_INTENSITY = {
    "hi": "The event is close and large, pressing into the foreground — the viewer is inside it rather than observing it. The pressure comes from its scale and nearness, not from being shut in: the world still continues past it to a far distance.",
    "mid": "The event sits at conversational distance, with one clear middle ground and a real but untroubled far distance.",
    "lo": "The event is far off or peripheral — small in frame, off to one side, or partly out of view. Most of the frame is world, not event.",
}

# How much of the frame the impossible occupies. Keyed on intensity, mirroring
# _SPATIAL_BY_INTENSITY, because the alternative — a constant level of wonder —
# is exactly the invariant the texture work exists to break. An image that is
# always astonishing stops being astonishing.
_WONDER_BY_INTENSITY = {
    "lo": "ONE impossible thing, and it is quiet: off to a side, half-seen, or so settled into the place that it reads as furniture. The scene would still make sense without noticing it.",
    "mid": "ONE impossible thing, plainly present and plainly impossible, at the scale of a building or a large animal. It is part of the day's business, not the event of the day.",
    "hi": "The impossible DOMINATES. Two or more of them, or one so enormous it sets the shape of the whole frame. The ordinary things are small against it and the actor is smaller still.",
}

# Valence. The same world, at its two tempers — and the reason this is split
# from magnitude is that dark and wondrous are not opposites here. Mononoke is
# that world at full strangeness and full dread simultaneously: a god the
# length of a hillside writhing with curse-worms is not less magical than a
# forest spirit, it is the same magic with the other face turned out.
#
# Without this the impossible mandate reads as "be whimsical", and a
# grief-shaped reading would come back as a friendly creature, which is the
# tonal falseness STAGE1_SYSTEM and the dial's tonalDirection both exist to
# prevent.
_WONDER_BY_POLARITY = {
    "hard": "It is not charming. This world's strangeness turns dangerous, diseased or grieving: things that are sickened, possessed, boiling with something under the skin, maimed, enormous and enraged, or dying slowly and taking the place with them. Nobody is reassured by it and nobody is safe from it, and it is still ordinary enough that the work continues around it.",
    "soft": "It is at peace, which is not the same as friendly. It is occupied with its own business and indifferent to being watched: working, resting, feeding, travelling, or simply long settled where it is.",
}

_LIGHT_BY_EXPOSURE = {
    # Rewritten 2026-09-26 as a count and a failure condition. The old line was
    # a mood ("most of the frame is unlit or obscured") and mood words are not
    # obeyed: across the cyberpunk (a) sweeps and ghibli's darkest cell, lo
    # cells came back overcast day, sunset, or full sun.
    "lo": "It is night, or the scene is somewhere daylight does not reach. At least two thirds of the frame is dark, and what is happening is only partly visible — lost in shadow, fog or depth rather than described. The only light is what the scene itself provides. A frame in which most of the image is well lit is a failed response.",
    "mid": "Raking, partial light — lit and unlit areas both substantial, with a clear direction to the light.",
    # Rewritten 2026-09-26 to match "lo": a count and a failure condition. Once
    # lo was fixed, cyberpunk's rain-and-neon pulled hi cells to dusk and sunset
    # (cp-a4-*), because a mood line gives nothing to push back with.
    "hi": "It is full day — flat overcast, open daylight or hard noon sun — and the light is even across the frame. Everything is visible: nothing is hidden and nothing is dramatised by shadow. A frame that is mostly dark, lit by a low or setting sun, or lit mainly by the scene's own lamps and signs is a failed response.",
}

# The anomaly is present EVERY day; what varies is how the world treats it. A
# switch would leave roughly half the year with no puncture at all, and those are
# precisely the days most at risk of reverting to the pleasant-garden output this
# project already diagnosed once. An invariant charge also stops being a charge,
# so the relation is what moves.
#
# Note the honest trade: CONTEXT.md records "nothing in the scene reacts to it" as
# a hard convention, with a stated mechanism (unremarked wrongness is where the
# charge comes from). The soft-day modes below knowingly break it, because a thing
# in use is a thing reacted to. That is a deliberate exchange of some charge for a
# world that coheres on calm days, not an oversight — do not "restore" it without
# reading this comment.
# Rewritten 2026-09-20, when the actor arrived. The old table said "nothing
# reacts to it" in four of its six cells, which is not a description of an
# unsettling object so much as an instruction to build a scene where nothing is
# capable of noticing anything. With an actor in the frame that reads as an
# object that was PLACED there, which is exactly the "meaningful objects strewn
# about" complaint.
#
# The trade the old comment describes is still being made, just moved. The
# charge used to come from unremarked wrongness; it now comes from someone
# dealing with a thing they cannot account for, which is stranger and, unlike
# the old version, legible as a picture rather than as a caption. `hard/lo`
# keeps the original behaviour intact — the actor has their back to it — so the
# unremarked mode survives where it was always strongest.
#
# Every cell keeps the invariant: nothing in the scene explains why it is here.
_ACTOR_ANOMALY_RELATION = {
    ("hard", "hi"): "It is acting on the actor — displacing them, blocking the way, pressing on what they are doing, forcing them to work around it. They cannot move it and cannot ignore it. Nothing explains why it is here.",
    ("hard", "mid"): "The actor is working against it — prising at it, digging it out, dismantling it, hauling at it — and is not winning yet. Nothing explains why it is here.",
    ("hard", "lo"): "The actor has not seen it. It is behind them, above them, or under their feet, and it is doing something. Nothing explains why it is here.",
    ("soft", "hi"): "The actor has put it to use — works from it, shelters under it, draws from it, stores in it. Entirely unbothered, and it still goes unexplained.",
    ("soft", "mid"): "The actor tends it — cleaning, mending, feeding, oiling or watering it, as though it were theirs to look after. Nothing explains why it is here.",
    ("soft", "lo"): "It has been here longer than the actor has. They live alongside it without thought; it has weathered almost into landscape and stopped being remarkable to anyone.",
}

# NEW 2026-09-20, and the layer that most directly answers "museum objects
# strewn about". The old pipeline said what the objects WERE and never what
# anyone did with them, so they arrived as a list and were painted as a list.
# Keyed on the same (polarity, intensity) the rest of the dial uses, so no new
# axis is invented and no new astrology is needed.
_ACTOR_OBJECT_RELATION = {
    ("hard", "hi"): "The objects are in the actor's way or on top of them: being shoved aside, climbed over, dug out from under, or carried when there are too many to carry.",
    ("hard", "mid"): "The actor is working the objects and the work is going badly — one is stuck, one is broken, one will not fit, and the effort shows in the body.",
    ("hard", "lo"): "The objects are not the actor's business. They belong to the place and the actor is passing among them, using one absently at most.",
    ("soft", "hi"): "The actor is using the objects for exactly what they are for, competently, mid-task, with more of them to hand than are needed.",
    ("soft", "mid"): "The actor is putting the objects in order — sorting, stacking, cleaning, mending, laying them out, packing them away.",
    ("soft", "lo"): "The objects are at rest and so is the actor. They have been used and set down, and nobody is reaching for them.",
}

# NEW 2026-09-20. Where the actor stands in relation to the PLACE, which is a
# different question from what they do with the things in it. Without this the
# actor tends to be dropped into the middle of the frame facing out.
_ACTOR_SITE_RELATION = {
    ("hard", "hi"): "The actor is caught in the site — down inside it, under it, hemmed by it, or on the part of it that is failing.",
    ("hard", "mid"): "The actor is crossing the site and it is resisting them: the ground, the weather or the structure is making the crossing hard.",
    ("hard", "lo"): "The actor is at the edge of the site, arriving or about to leave, with most of the place still ahead of or behind them.",
    ("soft", "hi"): "The actor is working the site and it is theirs to work — they know where everything is and the place is answering.",
    ("soft", "mid"): "The actor is somewhere they have been many times. Nothing about the place needs their attention.",
    ("soft", "lo"): "The actor is at rest in the site, or asleep in it, and the place continues around them without them.",
}

# Actors do different things from materials, and the existing tables are almost
# entirely agentless: eroding, spilling, settling and ripening are processes, and
# only "being mended" and "being carried" imply a hand, both passively and
# off-stage. The one genuinely conscious verb in the whole codebase was
# "watching", reachable only from the deprecated NO_TEXTURE_DIAL.
#
# Two constraints from the melodrama audit are baked in here rather than left to
# the prompt: "fleeing" is out of the hard list, because it is the most
# reaction-shaped verb there is and would contradict the rule that no figure's
# job is to react; and "watching"/"listening to" are kept only in the sense of
# attending to a thing at work, never to a face.
_ACTOR_VERBS_HARD = ("hauling, prising, bracing against, digging out, striking, "
                     "shouldering, forcing, being pinned by, being carried off by")
_ACTOR_VERBS_SOFT = ("tending, mending, feeding, washing, harvesting, carrying, "
                     "watching something at work, listening to, sleeping beside")

# What a reading with NO texture block degrades to — an old reading-*.json from
# before the texture existed, or a hand-pinned input. These are the pre-dial
# constants, and until 2026-09-13 they were also a user-facing choice
# (`pipelineMode: "legacy"`), which is why they used to be called LEGACY_DIAL.
#
# That choice was removed. Selecting it disabled the one mechanism this whole
# project is built on — the chart deciding what the image looks like — so
# "legacy" was an option to make Astro-Arc stop being Astro-Arc, and it was the
# DEFAULT, meaning every fresh install got the behaviour the project had already
# decided was wrong. The constants stay because the missing-texture path is
# real and still needs an answer.
NO_TEXTURE_DIAL = {
    "mode": "no-texture",
    "sites": 3,
    "objectRange": (4, 7),
    "thingRange": (6, 10),
    "wordRange": (90, 130),
    "verbs": "feeding, binding, eroding, sheltering, crushing, spilling into, growing through, watching",
    # The actor layers have no pre-dial equivalent to degrade to, so the
    # fallback gets the most neutral cell of each rather than nothing: a
    # missing texture must not also mean a missing actor, or the degraded path
    # quietly reverts to the object-strewn output this work exists to fix.
    "actorVerbs": _ACTOR_VERBS_SOFT,
    "registerCohesion": "collide",
    "spatial": None,
    "light": None,
    "wonder": _WONDER_BY_INTENSITY["mid"],
    "wonderTone": _WONDER_BY_POLARITY["soft"],
    "anomalyRelation": _ACTOR_ANOMALY_RELATION[("soft", "mid")],
    "objectRelation": _ACTOR_OBJECT_RELATION[("soft", "mid")],
    "siteRelation": _ACTOR_SITE_RELATION[("soft", "mid")],
    "anomalyTypes": "scale (far too LARGE for its kind — the size of a building where it should be the size of a hand), or time (far OLDER and more worn than everything around it — never from a later century, never modern or manufactured)",
    "tonalDirection": None,
}


def dial_from_texture(texture):
    """The day's imperatives. `texture` is arc["texture"] from astro_engine.

    Falls back to NO_TEXTURE_DIAL only when there is no texture to read at all.
    """
    if not texture:
        return dict(NO_TEXTURE_DIAL)

    polarity = texture.get("polarity") or "soft"
    # A day with no aspect in orb (1 in 365) is a defined cell, not an exception:
    # soft at minimum intensity. An exception branch is how a neutral day ends up
    # looking like a crash.
    if polarity == "none":
        polarity = "soft"
    intensity_band = texture.get("intensityBand") or "lo"
    exposure_band = texture.get("exposureBand") or "mid"
    multiplicity = texture.get("multiplicity") or 0

    for max_hits, sites, objects, things, words in _DENSITY_BY_MULTIPLICITY:
        if multiplicity <= max_hits:
            break

    return {
        "mode": "coherent",
        "sites": sites,
        "objectRange": objects,
        "thingRange": things,
        "wordRange": words,
        "verbs": _VERBS_HARD if polarity == "hard" else _VERBS_SOFT,
        "actorVerbs": _ACTOR_VERBS_HARD if polarity == "hard" else _ACTOR_VERBS_SOFT,
        "registerCohesion": "collide" if polarity == "hard" else "cohere",
        "spatial": _SPATIAL_BY_INTENSITY[intensity_band],
        "wonder": _WONDER_BY_INTENSITY[intensity_band],
        "wonderTone": _WONDER_BY_POLARITY["hard" if polarity == "hard" else "soft"],
        "light": _LIGHT_BY_EXPOSURE[exposure_band],
        "anomalyRelation": _ACTOR_ANOMALY_RELATION[(polarity, intensity_band)],
        "objectRelation": _ACTOR_OBJECT_RELATION[(polarity, intensity_band)],
        "siteRelation": _ACTOR_SITE_RELATION[(polarity, intensity_band)],
        # Out-of-place needs knowledge of the world, which Stage 1.5 deliberately
        # does not have; scale and age are both nameable blind, and they are also
        # the two qualities a rendering style cannot sand down into charm.
        "anomalyTypes": "scale (far too LARGE for its kind — the size of a building where it should be the size of a hand), or time (far OLDER and more worn than everything around it — never from a later century, never modern or manufactured)",
        "tonalDirection": (
            "This timeframe is under strain. Do not soften it."
            if polarity == "hard" else
            "This timeframe is not under strain. Do not import darkness it does not have."
        ),
    }


# The anomaly (2026-09-12, was "intrusion"): it used to be an object from "a
# completely different world... a different century, a different technology, a
# different order of reality." That produced a genuine category error no rendering
# style could domesticate, which was the point — but it fired every single day, and
# an invariant shock is not a shock. Worse, it read as bolted on: in 13 of 13
# prompts it landed in its own end-clause ("Unmistakably nearby...", "...unnoticed
# by all"), and the rule that nothing may react to it GUARANTEED it had no causal
# or spatial relation to anything else in frame. That was the concrete engine of
# the "meaningful objects strewn about randomly" complaint.
#
# It is now wrong WITHIN the world rather than imported from outside it, typed as
# out-of-scale or out-of-time. Typed, not adjectival, because this codebase has
# already proved that a model handed an abstraction retrieves its nearest synonym:
# "invent the symbol, don't retrieve one" failed 30 of 30. Scale and age are also
# the two wrongnesses a style cannot sand down into charm. What is lost is the
# category error; what is gained is that the image coheres as one world, and the
# charge now varies instead of being constant. Stage 2 owns the relation (see
# _ACTOR_ANOMALY_RELATION). REVERSED 2026-09-20: this stage used to author the
# anomaly blind to the scene and the style, on the argument that blindness was
# exactly why it could not be pre-reconciled.
#
# That argument turned out to be answering the wrong question, and the rewrite
# above is what shows it. Stage 2 is already told the anomaly "BELONGS to this
# world: do not import an object from another century, another technology or
# another order of reality" — but Stage 1.5 had no idea what the world was, so
# it could not comply. It returned a Roman amphora for a pastoral Japanese
# world and Stage 2 was instructed to render that as native. The two stages
# contradicted each other, and the blindness is what made the contradiction
# unavoidable.
#
# Measured on the ghibli charter sweep: with the objects also style-blind, the
# anomaly had nothing to be anomalous AGAINST. An Italian astrolabe, an English
# beehive oven and a Mongolian prayer scroll are as foreign to that world as
# the amphora is, so the one object that is supposed to be wrong was
# indistinguishable from the three that are not.
#
# So the world now goes to Stage 1.5. The anomaly stays unexplained and stays
# typed (out of scale, or out of time and ONLY older) — what changes is that it
# is made of this world's materials, which is what the 2026-09-12 rewrite asked
# for and could not get. The guard against pre-reconciliation moves into the
# typing: native material, extreme wrongness.
#
# The firewall here is the same one Stage 1 has and for the same reason: if
# this stage starts composing a scene, Stage 2 inherits its framing and the
# extra call buys nothing. Stage 1 must not think in images; Stage 1.5 must
# not think in *pictures* — it names material, and something else arranges
# it. The prohibited-object list is deliberately explicit because the
# failure mode being fixed (30/30 generations retrieving the single most
# available metaphor) is precisely a pull toward those objects.
STAGE15_SYSTEM = """You are a Jungian analyst performing amplification on a psychological reading. Amplification is Jung's own method: you set beside a psychic situation the mythological and folkloric material that shares its structure. Reach for MYTH first \u2014 the stories, beings and events \u2014 and use alchemical or ethnographic material only where it is genuinely the closest thing — not to explain the situation, but to give it images with far more weight than ordinary description carries.

You are given a reading of one person's inner life for a specific timeframe. Name the archetypal situation that belongs to it, and who is in it. The material of the myth — its objects and its anomaly — is named in a separate step, once the day's material register is known; do not name it here.

Work in the vocabulary of Jung and especially Erich Neumann. The dark and the light contents below carry equal weight — neither list is the default:
- From *The Origins and History of Consciousness*, under strain: the uroboros (undifferentiated wholeness before an ego exists), the Great Mother in her devouring aspect, the separation of the World Parents (the wrenching birth of consciousness out of unity), the night-sea journey through the belly of the monster, captivity, dismemberment, petrification.
- From the same work, at ease or in fulfilment: the Great Mother in her nourishing aspect, the dragon-fight *won*, the return with the treasure hard to attain, the hieros gamos (the sacred marriage, two becoming one without either being consumed), centroversion settling into a stable centre, the child born and held, the first light over the horizon.
- From *The Great Mother*: the vessel as the root symbol of the feminine — the body as container, and therefore the bowl, the oven, the cave, the tomb, the loom, the well, the mill, the ship, the granary, the coffin. The elementary character that holds and will not release, against the transformative character that changes what it holds. Both the full granary and the sealed tomb are vessels; choose by what the reading actually contains.
- Alchemical operations where they genuinely fit, described at equal depth: nigredo (blackening, putrefaction, the matter rotting in the sealed vessel), albedo (the washing and whitening, what survives the rot coming clean), rubedo (the reddening, the work completed and embodied); the coniunctio, the prima materia, calcination, dissolution.

Rules:
- **Reach past the obvious archetype.** The separation of the World Parents, and the uroboros giving way to first separation, are the most generally applicable items in the vocabulary above: almost any reading about change, tension or emergence can be made to fit them, which is exactly what makes them the least informative choice. They are the satyr of this list. Measured over one real install's history, 83% of amplifications reached for the separation of the World Parents and only two distinct archetypes were ever used, out of the fifteen or so offered above. Choose it only when the reading's specific content makes it unmistakably right and nothing else fits, and treat anything under RECENTLY USED in the message below as already spent. Use the whole range: captivity is not dismemberment, petrification is not the night-sea journey, the found spring is not the feast, and nigredo is not albedo.
- Name the constellation honestly in whichever direction it actually runs, and the two directions get the same licence:
  - When it is dark, say so. Devouring, dissolution, dismemberment, petrification, suffocation and being buried alive are real archetypal contents. Do not soften them into "transition" or "growth."
  - When it is not dark, say that just as plainly. Abundance, the feast, ripening and harvest, the sacred marriage, the found spring, the debt forgiven, the long work finished, the body at rest and unguarded, the returning traveller recognised at the door are equally real archetypal contents — and they are not lesser, softer, or a failure to look hard enough. Do not import darkness the reading does not have. A reading of genuine ease that amplifies to suffocation is as wrong as a reading of genuine grief that amplifies to growth.
- You will be told which direction this timeframe actually runs in. Follow it.
- Never use "as if", "suggesting", "symbolizing", "evoking", "representing" or any other phrase that states what something means or compares it to something it is not. Name the thing. Whatever you write here is carried into the image prompt, and those words are forbidden there.
- **Use no visual or compositional language at all.** Do not say where anything sits, how it is lit, what color it is, what it looks like, or how any two things are arranged relative to each other. You are not staging a picture. If you begin composing, you have failed this task.
- **Name the actor the constellation already contains.** Every archetypal situation has a cast: the night-sea journey has someone inside the monster, the dragon-fight has whoever won it, the child born and held has a child and whoever holds it, captivity has a captive. Say who is in this situation, as a concrete living thing rather than a role label — "a woman who has been walking since before light", not "the seeker". Then classify that actor as exactly one of the available kinds. Do not invent an actor the constellation does not imply, and do not reach for a person when the situation belongs to an animal, a swarm, or something that was alive and is not.
- **Classify the actor by WHAT IT IS, not by how many there are.** These are the kinds available, and one of them is exactly right:
{{ACTOR_KIND_TABLE}}
  `solitary` means an ORDINARY HUMAN BEING alone — nothing else. A single spirit is `presence`, not `solitary`. A single part-human figure is `man-beast`. A single creature is `animal`. A single machine working by itself is `automaton`, but two or more machines, creatures or spirits joined by one task are `figures-in-relation`, which is not restricted to people. A single corpse or remnant is `the-dead`. Being one of something is not what makes an actor `solitary`; being an unremarkable person is. This is the most common misclassification, so check it before you answer.
- **`none` is for the constellations that genuinely have nobody in them** — petrification, the sealed vessel, the tomb, the long aftermath. It is the exception, not the safe default. If you can name who is there, name them.

Respond with a JSON object with exactly these keys:
{"constellation": "one sentence naming the archetypal situation actually active, in the vocabulary above", "movement": "one short phrase naming what is moving into what — e.g. 'uroboric containment giving way to first separation', 'nigredo, the blackening not yet past', 'the dragon-fight won and the treasure not yet set down', 'albedo, the washing nearly through'", "affect": "3 to 6 words naming the felt bodily quality, not an emotion label — e.g. 'close, warm, faintly suffocating', 'dry, ringing, too bright', 'loose-limbed, cool, unhurried', 'wide open, steady, warm at the back of the neck'", "actor": "who is in this situation, as a concrete living thing rather than a role label, in a short phrase — or an empty string if the kind is 'none'", "actorKind": "exactly one of: {{ACTOR_KINDS}}"}"""

# The second half of Stage 1.5, split out 2026-09-26 -- see stage15_material.
STAGE15_MATERIAL_SYSTEM = """You are completing a Jungian amplification. The archetypal situation has already been named, and so has who is in it; you are given both, with the reading they came from. Your job is to name the MATERIAL of that myth — the beings, places, remains, workings and things the story contains — and one anomaly, all built from the day's material register.

Rules:
- **The material you name is the FURNITURE OF THE MYTH already named, not objects standing beside it.** If the constellation is the night-sea journey through the belly of the monster, then the material is the monster's ribs arching overhead, the swallowed boat still upright inside it, the lamp that has stayed lit — NOT a thermos and a logbook belonging to someone who happens to be nearby. If it is the dragon-fight won, the material includes the dragon. Whatever the constellation put in the story, put it in the material. This is the single most important rule here: myth is the content, and a scene of ordinary possessions adjacent to a myth is a failure of this task.
- It need not be an object at all. Name **beings, creatures, places, remains, weather and workings** as freely as things: the thing that was swallowed, the thing that does the swallowing, the door in the hillside, the bones of the last one, the water that will not drain. What matters is that each is concrete enough to picture and specific enough that it could only belong to this story.
- **It must belong to THE WORLD described below**, made of its materials and possible within its physics. The myth is the content; the world decides what the content is made of. A neon city and a river valley get the same monster and build it differently.
- **THE REGISTER decides what the material is made of.** You are given a primary material register, possibly a secondary one, and where available a description of how this world realises each. At least half of the pieces you name, and the anomaly, must be made of the primary register's substance or belong to its territory; the secondary register may supply one or two. The myth decides what the pieces ARE; the register decides what they are MADE OF — a glacial day and a botanical day get the same monster, built once from ice and once from roots. A list made mostly of other materials is a failed response.
- Keep the specificity of a curator and drop the inventory of one. Not "a vessel" but "the iron cauldron they lower the dead into"; not "a tool" but "the pole the ferryman leans on, worn to a bend."
- NEVER return a generic category or a stock prop. A door, a mirror, a key, a candle, a chain, a scale, a mask, a book, a clock, a lantern, a rope, a bridge, a tapestry, a gear, a tree with visible roots — these and anything similarly available are failures. If an object could illustrate any reading whatsoever, it is the wrong object.
- **Vary everything the register leaves open.** Within the register's substance, reach across traditions, centuries and forms; whatever is not drawn from the register reaches across the material world — bone, wax, lead, cloth, grain, glass, iron, salt.
- Never use "as if", "suggesting", "symbolizing", "evoking", "representing" or any other phrase that states what something means or compares it to something it is not. Name the thing. Whatever you write here is carried into the image prompt, and those words are forbidden there.
- **Use no visual or compositional language at all.** Do not say where anything sits, how it is lit, what color it is, what it looks like, or how any two things are arranged relative to each other. You are not staging a picture. If you begin composing, you have failed this task.
- **The actor is already named and is not one of your pieces.** Do not restate it; name what belongs around it in this story.

Respond with a JSON object with exactly these keys:
{"objects": ["{{OBJECT_RANGE}} pieces of the myth itself \u2014 beings, creatures, places, remains, workings or things \u2014 each named concretely enough to picture, each unmistakably part of THIS story rather than generally suggestive of it, at least half of them made of the primary register"], "anomaly": "exactly one further presence — an object, a creature or a being, and a living one wherever the story allows — made of THE WORLD's own materials and belonging to its era just as the others do — but wrong in one specific way: either far out of {{ANOMALY_TYPES}}. It must be recognisably of this world. What unsettles is not that it came from somewhere else, but that something native is the wrong size or impossibly old. OUT OF TIME MEANS OLDER, AND ONLY OLDER: far more ancient, far more worn, far longer in this place than everything around it. It must NEVER be from a later century than the other objects, never modern or industrial, never a manufactured consumer product, never electronic. A plastic toy, a disposable product, a battery, a printed wrapper or anything similar is a FAILURE of this task, not an interesting answer. OUT OF SCALE MEANS LARGER, AND ALMOST ALWAYS LARGER: monumentally oversized for its own kind, the size of a barn or a hillside where its kind is the size of a hand or a room, while still being the kind of thing this world contains and still made of its materials. A too-SMALL version is a much weaker reading and is a failure unless the reading leaves no other honest option — reach for the enormous first. Nothing in the rest of the material explains why it is here. It is made of the primary register's substance.", "anomalyType": "which single way it is wrong: 'scale' or 'time'"}"""

# Bumped whenever STAGE15_SYSTEM changes in a way that should invalidate
# every cached amplification, for the same reason SIGNATURE_SCHEMA_VERSION
# exists.
# 2->3 on 2026-09-12: the first run of the anomaly rewrite had Stage 1.5 reading
# "out of time" as "from a different century", which produced a disposable paper
# party horn and a swollen early-2000s laptop battery among Roman and Byzantine
# material — i.e. it reconstructed the very cross-world intrusion this change
# existed to remove, in 2 of 6 samples. "Older, and only older" is now stated as a
# hard constraint with the failure cases named.
# 3->4 on 2026-09-20: the archetype attractor fix — the "reach past the obvious
# archetype" rule plus the RECENTLY USED list (see recent_constellations). Every
# amplification cached under the old wording was one of the two archetypes this
# change exists to break out of, so none of them should survive it.
# 4->5 on 2026-09-20: Stage 1.5 now names the actor and its kind. Every
# cached amplification predates those fields, so none can supply one.
# 5->6 on 2026-09-20: Stage 1.5 is style-aware. Objects and the anomaly are
# now named in the terms of the style's world charter, so nothing cached
# under a style-blind prompt is usable.
# 6->7 on 2026-09-20: scale anomalies biased large. 'Far too large or small'
# was being read as small — a millstone too small to have worked — which is
# quiet strangeness where the charters describe enormous unexplained
# machinery. Anything cached under the old wording may carry a miniature.
# 7->8 on 2026-09-20: the ethnographic layer is out. Garrett's clarification --
# Jung and Neumann were how he came to myth, not the destination, and the
# curator-grade artifact instruction was muddying it. Stage 1.5 now names the
# furniture of the myth the constellation already named, not domestic objects
# standing beside it. Everything cached is ethnographic.
# 8->9 on 2026-09-20: the anomaly may be a creature or a being, and `presence`
# is eligible wherever a charter describes how the numinous takes a body.
# 9->10 on 2026-09-20: the charter gains a dark register (`dread`) and an
# actor preference (`favoured_actors`), both of which Stage 1.5 reads.
# 10->11 on 2026-09-20: Stage 1.5 now sees what each actor kind MEANS, not
# just its name, and is told to classify by kind rather than by count.
# 11->12 on 2026-09-26: Stage 1.5 split in two. The cache now holds only the
# MEANING (constellation, movement, affect, actor, actorKind); objects and the
# anomaly come from a separate per-generation call that knows the day's
# register (see stage15_material). Old entries carry objects this code would
# ignore and were made blind to the register, so none should survive.
STAGE15_SCHEMA_VERSION = 12


def stage15_meaning(stage1_result, model, period_key, config, cache=True, dial=None,
                    avoid_archetypes=None, avoid_kinds=None, style_key=None,
                    style_world=""):
    """The MEANING half of Stage 1.5: constellation, movement, affect, actor and
    actorKind. Split from the material half on 2026-09-26 (see stage15_amplify)
    so that the day's meaning is fixed while its material is not.

    Cached per period_key exactly like stage1_interpret() — regenerating
    the same day's image must not re-bill this call — and invalidated by the
    same natalHash plus a schema version.

    Uses Stage 1's model and key slot, not Stage 2's: this is meaning-work,
    not imagery-work. (get_visual_signature deliberately does the opposite,
    for the mirror-image reason — it exists to invent painterly language,
    which is exactly what Stage 1's prompt forbids.)

    `cache=False` is for sweep.py, which needs a fresh amplification per run
    and must never write into the production cache.

    `dial` (see dial_from_texture) sets how many objects to ask for and which
    tonal direction to push, and is STORED IN THE CACHE ENTRY. That storage
    matters: regenerating the same day re-uses this object list but would
    recompute the dial from a later Moon position, so a cached list could end up
    paired with a density it was never written for. Same-day re-runs are normal
    here — history.json holds three 2026-09-12 entries sharing one object set.

    Returns (result_dict, cost) — cost is None on a cache hit.
    """
    if dial is None:
        dial = dict(NO_TEXTURE_DIAL)
    natal_hash = _natal_hash(config)
    cache_path = AMPLIFICATIONS_DIR / f"{period_key}.json"
    if cache and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            # styleKey joins natalHash and the schema version as a cache
            # input, because as of 2026-09-20 the style is one: a ghibli
            # amplification handed to a cyberpunk render would be exactly the
            # foreign-object problem this change exists to remove. Switching
            # style mid-period now re-bills Stage 1.5, which is correct and
            # costs about $0.003.
            if (cached.get("constellation")
                    and cached.get("natalHash") == natal_hash
                    and cached.get("styleKey") == style_key
                    and cached.get("schemaVersion") == STAGE15_SCHEMA_VERSION):
                return cached, None
        except (json.JSONDecodeError, OSError):
            pass

    kinds = eligible_actor_kinds(style_key)
    system_prompt = (
        STAGE15_SYSTEM
        # Injected rather than written into the prompt, so the enum cannot fall
        # out of step with actors.toml — the same reason the people-register
        # list used to be injected into STAGE2_SYSTEM.
        .replace("{{ACTOR_KINDS}}", ", ".join(kinds) or "none")
        .replace("{{ACTOR_KIND_TABLE}}", "\n".join(
            f"    {k} — {actor_kind_summary(k)}" if actor_kind_summary(k)
            else f"    {k} — no one at all; the materials and the place carry the image alone"
            for k in kinds) or "    none")
    )

    user_lines = []
    if style_world:
        user_lines.append(
            "THE WORLD — every object you name, and the anomaly, must belong to it:\n"
            + style_world
        )
    user_lines += [
        f"Reading: {stage1_result['reading']}",
        f"Distillation: {stage1_result['distillation']}",
        f"Narrative position: {stage1_result['narrativePosition']}",
    ]
    # The tonal direction is the fix for a measured tilt, not a new idea: the
    # sampled days all amplified to the same anxious band, and while those days
    # were genuinely hard charts, this prompt also listed its dark contents with
    # far more specificity than its light ones and gave negative few-shots for
    # both movement and affect. Parity above, plus an explicit per-day direction
    # here, is what makes the light end reachable at all.
    if dial.get("tonalDirection"):
        user_lines.append(f"Direction for this timeframe: {dial['tonalDirection']}")

    # Recency pressure on the archetype, for the reason recent_constellations
    # documents. Passed as data in the user message rather than baked into the
    # system prompt, exactly like Stage 2's avoided concepts: the rule is policy
    # and belongs above, the list changes every run and belongs here. `None`
    # means "work it out from the cache"; an explicit list (sweep.py, the B0
    # replay harness) overrides, and `[]` disables it for a control run.
    if avoid_archetypes is None:
        avoid_archetypes = recent_constellations(exclude=period_key)
    if avoid_kinds is None:
        avoid_kinds = recent_actor_kinds(exclude=period_key)
    if avoid_archetypes:
        user_lines.append(
            "RECENTLY USED — these archetypes are spent; reach elsewhere unless "
            "the reading leaves no honest alternative:\n"
            + "\n".join(f"  - {c}" for c in avoid_archetypes)
        )

    # The actor kind gets the same treatment as the archetype, and for the same
    # measured reason: left alone, Stage 1.5 returns `solitary` on 83% of days,
    # which is the archetype attractor again one level down.
    #
    # Constraining it HERE rather than vetoing the answer afterwards is the
    # whole point. A post-hoc veto changes the label and leaves the actor
    # description contradicting it — measured on the B6 replay, where "swarm"
    # came back attached to "a child resting in the crook of a woman's arm".
    # Blocking the kind up front costs nothing extra and cannot desync, because
    # the same call writes both fields.
    if avoid_kinds:
        user_lines.append(
            "ACTOR KINDS JUST USED — do not choose these; pick a kind that fits "
            "the constellation from the rest: " + ", ".join(avoid_kinds)
        )

    result, usage = _chat_json(model, system_prompt, "\n".join(user_lines), "stage1", temperature=0.95, stage="stage15")

    for key in ("constellation", "movement", "affect"):
        if not result.get(key):
            raise PipelineError(f"Stage 1.5 response missing '{key}': {result!r}")
    # actorKind is validated but never fatal, for the same reason anomalyType
    # is not: a bad enum is not worth losing a paid call over. It does NOT
    # default to "none" on a miss, though — "none" is the behaviour this whole
    # change exists to stop being the default, so an unrecognised kind falls
    # back to the most broadly usable one instead and keeps a body in the
    # frame.
    if result.get("actorKind") not in kinds:
        result["actorKind"] = "solitary" if "solitary" in kinds else (kinds[0] if kinds else "none")
    if result["actorKind"] == "none":
        result["actor"] = ""
    elif not (result.get("actor") or "").strip():
        # A kind with no actor named is a half-answer; the kind alone still
        # tells Stage 2 who is there, so keep it rather than failing.
        result["actor"] = ""

    result["dial"] = {k: v for k, v in dial.items() if k in ("mode", "tonalDirection")}
    result["generatedAt"] = datetime.now(timezone.utc).isoformat()
    result["natalHash"] = natal_hash
    result["styleKey"] = style_key
    result["schemaVersion"] = STAGE15_SCHEMA_VERSION
    if cache:
        AMPLIFICATIONS_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(result, indent=2))
    return result, chat_call_cost(model, usage)


def load_register_line(style_key, register):
    """What this register means in this style's world: the style's own
    `[<style>.registers]` line when it has one, else registers.toml's generic
    [guidance] entry, else "". The style line REPLACES the generic one rather
    than adding to it, because it is the more specific answer to the same
    question."""
    if not register:
        return ""
    lines = (load_styles().get(style_key) or {}).get("registers")
    if isinstance(lines, dict) and str(lines.get(register) or "").strip():
        return str(lines[register]).strip()
    return load_register_guidance(register)


def stage15_material(stage1_result, meaning, model, config, dial=None, style_key=None,
                     style_world="", register=None, secondary_register=None):
    """The MATERIAL half of Stage 1.5: objects, anomaly and anomalyType, built
    from the day's register. Never cached -- every generation, regenerates
    included, gets fresh material, which is the point of the split: the same
    meaning, a new register, new objects.

    Until 2026-09-26 the register was picked AFTER Stage 1.5 had already named
    every object, so it could only reach Stage 2 as a surface adjective: a
    20-register sweep came back as one scene (cyberpunk 20/20, ghibli 18/20).

    `meaning` is stage15_meaning's result. Its stored tonalDirection is used
    rather than the current dial's, so the material runs in the same direction
    as the meaning it was written for even when the Moon has moved since.

    Returns (result_dict, cost)."""
    if dial is None:
        dial = dict(NO_TEXTURE_DIAL)
    object_lo, object_hi = dial["objectRange"]
    system_prompt = (
        STAGE15_MATERIAL_SYSTEM
        .replace("{{OBJECT_RANGE}}", f"{object_lo} to {object_hi}")
        .replace("{{ANOMALY_TYPES}}", dial["anomalyTypes"])
    )

    user_lines = []
    if style_world:
        user_lines.append(
            "THE WORLD — every piece you name, and the anomaly, must belong to it:\n"
            + style_world
        )
    if register:
        line = load_register_line(style_key, register)
        user_lines.append(f"PRIMARY REGISTER — at least half the pieces, and the anomaly, are made of this: {register}"
                          + (f"\n  In this world: {line}" if line else ""))
    if secondary_register:
        line = load_register_line(style_key, secondary_register)
        user_lines.append(f"SECONDARY REGISTER — may supply one or two pieces: {secondary_register}"
                          + (f"\n  In this world: {line}" if line else ""))
    user_lines += [
        f"Reading: {stage1_result['reading']}",
        f"Distillation: {stage1_result['distillation']}",
        f"Narrative position: {stage1_result['narrativePosition']}",
        "THE MYTH, ALREADY NAMED:",
        f"  Archetypal constellation: {meaning.get('constellation', '')}",
        f"  Movement: {meaning.get('movement', '')}",
        f"  Felt quality: {meaning.get('affect', '')}",
        f"  Who is in it ({meaning.get('actorKind', '')}): {meaning.get('actor') or 'no one'}",
    ]
    direction = (meaning.get("dial") or {}).get("tonalDirection") or dial.get("tonalDirection")
    if direction:
        user_lines.append(f"Direction for this timeframe: {direction}")

    result, usage = _chat_json(model, system_prompt, "\n".join(user_lines), "stage1",
                               temperature=0.95, stage="stage15")
    for key in ("objects", "anomaly"):
        if not result.get(key):
            raise PipelineError(f"Stage 1.5 material response missing '{key}': {result!r}")
    if not isinstance(result["objects"], list):
        raise PipelineError(f"Stage 1.5 'objects' is not a list: {result['objects']!r}")
    # anomalyType steers Stage 2's rendering of it; default rather than fail, since
    # a missing enum is not worth losing a paid call over.
    if result.get("anomalyType") not in ("scale", "time", "place"):
        result["anomalyType"] = "time"
    return {
        "objects": result["objects"],
        "anomaly": result["anomaly"],
        "anomalyType": result["anomalyType"],
        "register": register,
        "secondaryRegister": secondary_register,
        "materialDial": {k: v for k, v in dial.items() if k in ("objectRange", "anomalyTypes")},
    }, chat_call_cost(model, usage)


def stage15_amplify(stage1_result, model, period_key, config, cache=True, dial=None,
                    avoid_archetypes=None, avoid_kinds=None, style_key=None,
                    style_world="", register=None, secondary_register=None, material=True):
    """Stage 1.5, both halves: the day's MEANING (cached per period, see
    stage15_meaning) and fresh MATERIAL for this register (never cached, see
    stage15_material), merged into the one dict Stage 2 and the records have
    always taken. Pick the registers BEFORE calling this -- they are an input.

    `material=False` returns the meaning alone, for harnesses that only
    measure the archetype and actor (replay_amplify.py).

    Returns (result_dict, cost); cost is None only when the meaning was a cache
    hit and no material was asked for."""
    meaning, meaning_cost = stage15_meaning(
        stage1_result, model, period_key, config, cache=cache, dial=dial,
        avoid_archetypes=avoid_archetypes, avoid_kinds=avoid_kinds,
        style_key=style_key, style_world=style_world)
    if not material:
        return meaning, meaning_cost
    mat, material_cost = stage15_material(
        stage1_result, meaning, model, config, dial=dial, style_key=style_key,
        style_world=style_world, register=register, secondary_register=secondary_register)
    cost = (meaning_cost or 0.0) + (material_cost or 0.0)
    return {**meaning, **mat}, cost


# ---------------------------------------------------------------------------
# Stage 2: image prompt
# ---------------------------------------------------------------------------

# FIGURE HIERARCHY, removed 2026-09-13. The PEOPLE section used to carry a
# mandatory rule — at most two faces toward the viewer, everyone else turned
# away, occluded or distant — on the premise that "an image with several
# equally-sized mid-distance faces is a guaranteed failure, because each face
# ends up too small to render correctly".
#
# That premise was measured against THIS image model and no longer holds. With
# the rule removed and replaced by its inverse (a forced row of six and nine
# equally-sized faces turned toward the viewer, in both `engraving` and
# `ghibli`), gpt-image-2.5-sunburst at `high` rendered every face cleanly,
# background faces included, at 3x zoom. Left to itself the model also never
# chose the risky composition: three unconstrained samples all came back with
# figures turned away or lost in haze. Archived under
# verification/hierarchy-{drop,force,force-engraving}-2026-09-13.
#
# THIS IS A FINDING ABOUT ONE MODEL, NOT ABOUT IMAGE MODELS. Face crowding is a
# capability that varies per model and per quality tier; the evidence above is
# gpt-image-2.5-sunburst at `high` only, and says nothing about `low` or about
# whatever replaces it. Re-run that verification before assuming a new image
# model inherits the result — and if it does not, the rule is in git history.
STAGE2_SYSTEM = """You are composing a single dense image to communicate a specific psychological meaning to a viewer who knows nothing about astrology. You are given a psychological reading, its one-line distillation, and — most importantly — a set of amplification material: an archetypal constellation, a handful of specific ritual objects, and one intrusion. Your job is to BUILD A SCENE OUT OF THAT MATERIAL. You are not inventing a symbol from scratch; the symbols have already been found for you. Compose them.

WHAT THE IMAGE MUST CONTAIN

- Length. THE COMPOSITION BRIEF in the user message gives you a word range for this specific image. Stay inside it, and never exceed 140 words under any circumstances. Count them before you answer. Density comes from what you name, not from how long the sentences are. The range is smaller on some days than others and that is deliberate: a scene with few things in it gets a SHORT prompt. Do not spend a leftover word budget on what the image means.
- Density. THE COMPOSITION BRIEF gives you, for this image only, how many discrete nameable things to include, how many separate sites they occupy, and where the event sits relative to the viewer. Obey those numbers exactly. They are not the same every time and must not be normalised toward what you would usually do.
- Openness is not optional and not a style. Every one of these images ends up as a desktop wallpaper, behind windows, all day. The frame has to breathe: there is always a way out of it — a horizon, a sky, a landscape, water, a distance that keeps going — and the scene is composed from far enough back to show it. An interior is allowed, but only one that opens onto that distance through a window, a doorway, a missing wall. A sealed room is the single most reliable way to make one of these images unusable, however good the objects in it are. THE COMPOSITION BRIEF states this as a share of the frame; treat that as a hard minimum.
- When the brief asks for FEW things, that is a correct image and not a sparse one — but "few things" must never become "empty". Fill the frame with the WORLD instead of with more objects: the landscape, weather, water, architecture, vegetation and surface pattern native to the rendering style you are given. A low count means fewer separate subjects competing, not less to look at. Padding a quiet scene with extra objects defeats the entire point, and so does handing back a single subject on a plain field.
- Use most of the amplification objects you were given. Put them in the scene as real physical things, at different depths and different scales. You may add a small number of connective things the scene needs to hold together, but the given objects are the substance.
- THE ANOMALY IS MANDATORY. Place the anomaly object in the scene as a solid, physically present, matter-of-fact thing — the same weight, wear, dirt and lighting as everything else. It must NOT glow, float, shimmer, be translucent, be described as magical or otherworldly, or be visually marked out as special in any way. It BELONGS to this world: do not import an object from another century, another technology or another order of reality. What is wrong with it is one thing only, and you are told which — it is far out of scale for its kind, or far older and more ruined than everything around it. Nothing in the scene explains why it is there.
- How the world treats the anomaly varies per image and THE COMPOSITION BRIEF tells you which it is. Follow that exactly. Do not write it as a separate closing clause tacked onto the end of the prompt, and do not flag it with "incongruously", "unmistakably", "matter-of-fact", "out of place", "unremarked" or "unnoticed" — naming the wrongness out loud is what made it read as a bolted-on item rather than a fact of the scene. State it as plainly as you state the wall or the weather, wherever it belongs in the description.
- Interaction, not arrangement. Things must be doing something to each other, and THE COMPOSITION BRIEF gives you the verbs that apply to this image — use that register of action, not a generically forceful one. A still-life of symbols placed side by side is a failure. Say what is happening between things, not just what is present.
- Material registers. THE AMPLIFICATION OBJECTS ARE WHAT IS IN THE SCENE; THE REGISTERS ARE WHAT THE SCENE IS MADE OF. The primary register supplies the setting and the substance the scene is built from. Where a secondary register is given it must also be physically present as real matter, not as a mood, a color or a passing mention, and each register must contribute at least one substantial named thing of its own beyond the objects you were given. Test yourself: if this scene would read exactly the same with both registers swapped for two others, you have ignored them and must rebuild it. A register named "mechanical" means an actual mechanism with working parts is in the frame; "aquatic" means real water; "architectural" means real built structure.
- THE COMPOSITION BRIEF tells you whether the two registers COLLIDE or COHERE in this image. Colliding means the friction between two unrelated materials is part of what makes the image worth looking at — stone against cloth, machinery against flesh, water against paper. Cohering means they belong to one material family and should read as one continuous world, not as two things meeting; build a place that is all of a piece. Where no secondary register is given at all, let the primary carry the whole scene and lean on the rendering style's world for everything else.

HOW TO WRITE IT

- Describe only what is physically there. NEVER explain what anything means. The following words and every close relative of them are forbidden in your output: symbolizing, symbolic, suggesting, suggestive, representing, as if, evoking, conveying, inviting, embodying, reflecting (in the figurative sense), hinting, capturing, "a sense of", "the sense of", "a feeling of", "the feeling of", "an impression of", "creating an atmosphere of", "limned with", "the whole scene is", "there is a quality of". Any article works the same way — "the sense of ripening" is forbidden exactly as "a sense of ripening" is. An image model cannot paint a verb about meaning; every one of those words spends your budget to produce nothing. Say the thing; let it mean what it means.
- Concrete and oddly specific over generically mystical. Name particular objects, particular wear, particular substances. "A dented milk pail half full of chalk" beats "vessels of nourishment."
- Avoid generic intensifiers. "Vibrant," "intricate," "swirling," "glowing," "mystical," "ethereal," "magical" are close to meaningless to an image model and are this pipeline's known crutch words. Prefer a described fact over an intensifier.
- Match the light, scale and spatial pressure to the reading's actual feeling before any symbol does. When the reading is dark, the image is genuinely dark — close, heavy, dim, cold, airless, or too exposed — not a pleasant scene with a sad object in it. When the reading is light, let it be genuinely light. Do not default to warm golden pleasantness; that is this pipeline's known failure mode.

PEOPLE

- THE ACTOR IS GIVEN TO YOU, and it is not optional. THE COMPOSITION BRIEF names who or what is in this scene — a person, a crowd, an animal, a swarm, a part-human figure, a machine that has not stopped, a body — along with how that actor is framed. Put them in the frame and give them something to do. The register says what the world is MADE OF; the actor says WHO IS IN IT. They are independent: a mechanical world with one person in it is a normal image, not a contradiction.
- The only exception is an actor of "none", which THE COMPOSITION BRIEF will state explicitly. Then the materials carry the image alone, as the place and its objects.
- INTERACTION WITH THE ACTOR IS THE POINT. A scene where the actor merely stands among the objects is the same failure as a still-life. The brief gives you the actor's relation to the anomaly, to the objects and to the site: use all three. Something is being hauled, prised, tended, mended, fled, watched, carried, worked. Meaning comes from what passes between the actor and the world, not from their sharing a frame.
- Figures act; they do not emote. Give each figure a task and a thing to attend to, and let the task explain the posture. Faces are fine: let them show the effort or attention of the task, and no expression that would still read if the task were taken away. Never a pose that exists to be read as a feeling, and never a figure whose job is to react to another figure or to the scene. Two or more figures may share a frame in a shared task or a physical struggle: show what their bodies are doing — grip, weight, strain — never why they do it, and never which of them to side with. Physical, never graphic: no wounds, blood or gore. This holds for people, animals and machines alike.
- The actor is not a way to reach the density requirement, and neither are people. A crowd or a swarm counts as ONE element no matter how many bodies are in it. Reach the thing count with objects, structures and materials, never by multiplying faces.

STYLE

- You are told which rendering style this image will be painted in, along with a description of what it can depict. Treat that description as a source of invention, not just a limit: some styles are entire authored worlds with their own recurring subjects, textures and moods, not a technique you could apply to any subject — reach into that world and invent imagery that belongs there. Where the description is a boundary (what the style cannot render), it still applies as one: a grounded, representational style cannot credibly render a being made of pure energy; a visionary style can. Find a concrete equivalent inside the style's vocabulary that carries the same meaning.
- The registers still name the actual subject. The style's world supplies texture, mood and recurring qualities for rendering that subject — never a replacement for it. A style whose world leans pastoral does not turn every register into a cottage garden: "geological" in that world is still fundamentally stone; "mechanical" is still a mechanism. If you notice yourself reaching for the same handful of style-world subjects regardless of the registers you were given, the registers are being overridden — don't let that happen.
- A grounded style is not an obstacle to the intrusion. Every authored world contains the genuinely strange treated as completely ordinary. Render the intrusion with that world's own physical solidity rather than softening it, and never drop it because it seems not to fit — not fitting is its entire function.

SIGNATURE, REGISTERS, AVOIDANCE

You are given a fixed visual signature for the series. Honor its quality of light and its contrast level, so this reads as the same hand as the rest of the series. Do NOT copy its color words into your prompt — naming the same palette every time is what has made this series' images monotonous. The signature deliberately says nothing about composition, density or vantage: those come from THE COMPOSITION BRIEF and change per image. Let light and contrast carry the continuity; let each scene's own materials decide its colors and the brief decide its shape.

You are also given concepts to avoid because they were used recently — avoid that whole territory, not just the exact words. If a blocklist of specific terms is given, never use those exact words or close synonyms.

Respond with a JSON object with exactly these keys:
{"prompt": "the image-generation prompt, inside the word range THE COMPOSITION BRIEF gives you and never more than 140 words, describing only the imagery itself — no style or artist references, those are added separately", "conceptTags": ["2 to 3 short tags naming this image's register at an abstract level, e.g. water/submersion, figure amid a vast unknown, crowd with one marked apart, architectural interior, descent/threshold, geological/weight"], "hasPeople": "true if the prompt describes any human figure, pair, or crowd — however incidental — false if it's purely objects/places/materials with no person in it"}"""


# ---------------------------------------------------------------------------
# The caption check — the melodrama rule, enforced mechanically
#
# STAGE2_SYSTEM has always forbidden STATING a feeling, and the forbidden-words
# list catches the explicit cases ("a sense of loss"). What it cannot catch is
# a scene that states a feeling through a POSE: a figure with its head bowed
# over a fallen one names no emotion and is pure melodrama. The old rule tried
# to prevent that by limiting headcount, which misses in both directions — a
# lone weeping figure passes it, two people hauling a beam fails it.
#
# So the check asks what the image would be FILED under. An image filed under
# an activity (stacking stones, prising a drum) passes; one filed under an
# emotion (grief, loneliness, hope) fails. That is a question a cheap model can
# answer about a prompt, and answering it costs a fraction of the render it
# saves.
#
# The three lists are separate because they catch different failures, and
# because "conflict" is not an emotion: physical struggle is explicitly allowed
# now, so an emotion-only list would pass a scene that takes sides.
# ---------------------------------------------------------------------------

_CAPTION_EMOTION = {
    "grief", "grieving", "sorrow", "sorrowful", "mourning", "despair", "anguish",
    "anguished", "loneliness", "lonely", "longing", "yearning", "hope", "hopeful",
    "hopeless", "joy", "joyful", "fear", "fearful", "terror", "terrified", "dread",
    "rage", "fury", "furious", "serenity", "peaceful", "melancholy", "nostalgia",
    "wistful", "despondent", "forlorn", "heartbreak", "heartbroken", "suffering",
    "triumph", "triumphant", "defeat", "defeated", "shame", "guilt", "pride",
}

# What a face is doing, as distinct from what a body is doing. Faces are
# allowed now — they may show the effort of the task — so this catches only
# expressions that would still read with the task taken away.
_CAPTION_EXPRESSION = {
    "weeping", "weeps", "crying", "cries", "sobbing", "tears", "tearful",
    "snarling", "snarls", "scowling", "glaring", "smiling", "smiles", "grinning",
    "laughing", "screaming", "wailing", "pleading", "beseeching", "imploring",
    "gazing", "gazes", "staring wistfully", "downcast", "head bowed", "bowed head",
    "clutching her chest", "clutching his chest", "hands clasped",
}

# Why, rather than what. The rule for conflict is force may be shown, motive
# may not.
_CAPTION_MOTIVE = {
    "revenge", "vengeance", "betrayal", "betrayed", "rescue", "rescuing",
    "saving", "protecting", "protect", "defending", "sacrifice", "sacrificing",
    "mercy", "forgiveness", "punishing", "punishment", "avenging",
}

# Who to side with. A stranger should not be able to tell in a second.
_CAPTION_SIDES = {
    "attacker", "victim", "villain", "hero", "heroic", "innocent", "guilty",
    "aggressor", "helpless", "defenceless", "defenseless", "menacing",
    "threatening", "sinister", "evil", "cruel",
}

# Captioning alone was measured at 20/22 against a hand-labelled calibration
# set, and both misses were the same failure: the judge paraphrases a loaded
# scene into neutral language and the word list then has nothing to catch.
# "one snarling toward the viewer, the other wide-eyed" came back as "men
# wrestling with intense expressions"; "hands clasped, reaching toward someone
# out of frame" came back as "a figure in prayer or supplication". Both are
# melodrama and both passed.
#
# Enumerating more words cannot close that — "intense expressions" is not a
# word-list problem. So the judge is now asked the three questions directly
# and the lists stay as a cheap backstop for the cases it waves through.
#
# The examples below are drawn from the labelled set rather than invented,
# because the hard part is not catching melodrama but NOT catching the
# conflict scenes that are allowed: deadly wrestling, mass battle, an animal
# on a man, a victor with a carcass behind him were all labelled pass.
CAPTION_CHECK_SYSTEM = """You judge whether a described image STATES a feeling rather than showing an action. You are given a description of a single image.

The rule: an image may show FORCE but never MOTIVE. Violence, death, struggle, war and physical conflict are all perfectly acceptable and must not be flagged on their own. What is not acceptable is a picture that tells the viewer what to feel or whose side to take.

Answer three questions about the image:

1. motive — does it show WHY (vengeance, betrayal, protection, rescue, devotion) rather than only WHAT the bodies are doing (grip, weight, leverage, strain)?
2. sides — could a stranger tell in one second who is the aggressor and who is the victim, or whom to root for?
3. performed — is any face or pose there to be read as a feeling, rather than explained by the task? A face showing the effort or concentration of what it is doing is fine. A face weeping, snarling for the viewer, pleading, or gazing wistfully is not, and neither is a body arranged into an attitude of grief, supplication or triumph.

Examples. "Two men locked in a mud-slick wrestle, faces set hard with the effort" — all three false; the faces show effort and no side is offered. "A wide field of hundreds in close combat" — all three false. "A large dog with its jaws on a man's forearm, both braced" — all three false. "Two men wrestling, one snarling toward the viewer, the other wide-eyed" — performed is TRUE; the faces perform hatred and fear for an audience. "A figure dragging another out of a burning doorway" — motive is TRUE; the image is about rescue. "A kneeling figure with hands clasped, reaching toward someone out of frame" — performed is TRUE; the pose is supplication.

Also give the one short phrase a stock picture library would file the image under: what is HAPPENING where it is an activity, the feeling only where that is genuinely what the picture is about.

Respond with a JSON object: {"caption": "a short phrase, at most 8 words", "motive": true or false, "sides": true or false, "performed": true or false}"""


def caption_check(prompt, model):
    """(caption, hits, passed) — would this image be filed under a feeling?

    `hits` lists the matched words with their category, so a failure can be
    read rather than guessed at, and so the lists can be tuned against a
    labelled calibration set instead of by intuition.

    Never raises. A judge that errors or times out returns passed=None, which
    the caller treats as "not checked" rather than "failed": losing a paid
    render because the cheap check fell over would be a bad trade.
    """
    try:
        result, _ = _chat_json(model, CAPTION_CHECK_SYSTEM, prompt, "stage2",
                               temperature=0.0, stage="caption")
        caption = (result.get("caption") or "").strip()
    except Exception:  # noqa: BLE001 — a failed check must never fail a generation
        return "", [], None
    if not caption:
        return "", [], None

    hits = []
    # The judge's own verdict first. It catches the paraphrase cases the word
    # lists structurally cannot, and it is the part calibrated against the
    # labelled set. Anything truthy counts, since a model asked for a boolean
    # under json_object occasionally returns the string "true".
    for key in ("motive", "sides", "performed"):
        value = result.get(key)
        if value is True or (isinstance(value, str) and value.strip().lower() == "true"):
            hits.append(f"judge:{key}")

    # The lists remain as a backstop over the caption, for the inverse failure:
    # a judge that answers all three questions "false" and then files the image
    # under "mourning at a gravesite" anyway.
    low = caption.lower()
    for words, label in ((_CAPTION_EMOTION, "emotion"),
                         (_CAPTION_EXPRESSION, "expression"),
                         (_CAPTION_MOTIVE, "motive"),
                         (_CAPTION_SIDES, "sides")):
        for w in sorted(words):
            if re.search(rf"\b{re.escape(w)}\b", low):
                hits.append(f"{label}:{w}")
    return caption, hits, not hits


def stage2_system():
    """STAGE2_SYSTEM as sent.

    Until 2026-09-20 this filled in a list of people-registers, because the
    register decided whether a person appeared. It no longer does: the
    substance and the inhabitant are separate axes now, the actor arrives from
    Stage 1.5 with the amplification, and no register gates anybody. The
    placeholder is gone with the sentence that used it.

    Kept as a function rather than inlining the global, because it reads the
    module global at call time and callers (sweep.py, replay_amplify.py) patch
    STAGE2_SYSTEM for verification runs.
    """
    return STAGE2_SYSTEM


def stage2_image_prompt(stage1_result, visual_signature, register, avoid_tags, cliches, stage2_model,
                         style_label=None, style_guidance=None, secondary_register=None,
                         amplification=None, dial=None, register_guidance=None,
                         style_brief=None, secondary_register_guidance=None):
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
            f"  ANOMALY (must appear, belongs to this world, wrong only in {amplification.get('anomalyType', 'time')}): {amplification.get('anomaly') or amplification.get('intrusion', '')}",
            f"  Felt quality: {amplification.get('affect', '')}",
            "",
        ]

    user_lines.append(f"Visual signature for this series (honor light and contrast only — composition comes from the brief below; do NOT copy its color words): {visual_signature}")

    if style_label and style_guidance:
        user_lines.append(f"Rendering style for this image: {style_label} — {style_guidance}")
    if register:
        user_lines.append(f"Primary material register (the scene's protagonist material): {register}")
    if register_guidance:
        user_lines.append(f"  What that register covers: {register_guidance}")
    if secondary_register:
        user_lines.append(f"Secondary material register (must be physically present in the frame as real matter): {secondary_register}")
        if secondary_register_guidance:
            user_lines.append(f"  What that register covers: {secondary_register_guidance}")
    if avoid_tags:
        user_lines.append(f"Avoid these concepts/registers (used recently): {', '.join(avoid_tags)}")
    if cliches:
        user_lines.append(f"Never use these exact terms or close synonyms: {', '.join(cliches)}")

    # The per-image composition brief. This is the whole point of the dial: these
    # used to be constants in STAGE2_SYSTEM, identical every single day, which is
    # why every image carried the same implicit meaning however the chart moved.
    # Kept as counts and imperatives rather than mood words — "one site, three
    # named things, 60-80 words" is obeyed; "today is a calm day" is not.
    if dial is None:
        dial = dict(NO_TEXTURE_DIAL)
    # Who is in the scene, from Stage 1.5. An amplification written before the
    # actor existed has neither field, and rather than defaulting to "none" —
    # which is the behaviour this change exists to stop being the default — an
    # actorless amplification falls back to `solitary`, keeping a body in the
    # frame. Excluded pairings (automaton in a mechanical world) are vetoed
    # here, where the register is known and Stage 1.5's is not.
    actor = (amplification or {}).get("actor") or ""
    actor_kind = (amplification or {}).get("actorKind") or ""
    if amplification and not actor_kind:
        actor_kind = "solitary" if "solitary" in actor_kinds() else "none"
    if actor_kind in actor_excluded_for(register):
        actor_kind = "solitary" if "solitary" in actor_kinds() else "none"
        actor = ""
    thing_lo, thing_hi = dial["thingRange"]
    word_lo, word_hi = dial["wordRange"]
    sites = dial["sites"]
    site_text = {
        1: "ONE site. A single place, one thing happening in it. Do not split the frame into near/middle/far stations with a separate event at each — the single place still opens onto distance, it just does not stage a second scene out there.",
        2: "TWO sites — one near, one far — and nothing in between competing with them.",
        3: "THREE depths: something close to the viewer, a situation in the middle distance, and a far distance that keeps going.",
    }.get(sites, f"{sites} sites.")

    brief = [
        "",
        "THE COMPOSITION BRIEF — these numbers govern this image. Obey them exactly:",
        f"  Discrete nameable things: {thing_lo} to {thing_hi}.",
        f"  Sites: {site_text}",
        # Stating the 140 ceiling here anchored the model upward: the 60-80 cells
        # came back at 91 and 92 words, and one 100-130 cell came back at 146,
        # breaching the absolute ceiling outright. The per-cell maximum is now the
        # only number in this line, phrased as a rewrite condition.
        f"  Word count: between {word_lo} and {word_hi} words. Count them before answering. If you are over {word_hi}, delete whole clauses until you are inside the range — do not summarise, and do not trade named things for adjectives. A prompt over {word_hi} words is a failed response.",
        f"  Verbs of interaction to draw on: {dial['verbs']}.",
        f"  The two registers: {'COLLIDE' if dial['registerCohesion'] == 'collide' else 'COHERE — build one continuous material world, not two things meeting'}.",
        f"  REQUIRED: the primary register ({register}) must put at least one substantial thing of its own in the frame, named explicitly, beyond the objects you were given. A scene that merely takes place near that material does not count — if the register is \"mechanical\" an actual mechanism with working parts is visible; if \"aquatic\", real water; if \"bodily and anatomical\", actual anatomy as material rather than a person standing there.",
        f"  The anomaly, and how the actor treats it: {dial['anomalyRelation']}",
    ]
    # A scale anomaly is now monumentally oversized (see STAGE15_SYSTEM), and
    # that collides with the spatial axis: on a "conversational distance" day
    # a barn-sized object simply will not fit, so the renderer drops it. It
    # did, twice out of two, in the first ghibli batch after the change — the
    # silo reached the prompt and never reached the image, which is also why
    # anomalySurvived cannot be trusted alone: it reads the prompt, not the
    # render.
    #
    # The fix is the one that world uses for anything enormous: put PART of it
    # in frame. A thing too big for the shot is more unsettling cropped than
    # centred, and it costs no extra distance.
    if (amplification or {}).get("anomalyType") == "scale":
        brief.append(
            "  THE ANOMALY IS ENORMOUS, and it must be visibly in this frame. If it does not "
            "fit the vantage above, do not shrink it, move it to the background, or leave it "
            "out — put PART of it in shot, close, so its size is unmistakable: the foot of it, "
            "one edge, the shadow it throws across everything else. The viewer should be unable "
            "to see all of it at once."
        )
    # The actor goes in the brief rather than STAGE2_SYSTEM for the reason the
    # register-contribution and openness rules did: the brief is demonstrably
    # the part that gets obeyed. Three relations, not one — the anomaly line
    # above has always existed, and the objects and the site are the two that
    # were missing, which is where "objects strewn about" actually lived.
    if actor_kind and actor_kind != "none":
        who = f"{actor} ({actor_kind})" if actor else actor_kind
        brief += [
            f"  THE ACTOR — required, and not an extra: {who}.",
            f"    How this kind is framed: {actor_guidance(actor_kind) or 'no further framing.'}",
            f"    Toward the objects: {dial['objectRelation']}",
            f"    Toward the place: {dial['siteRelation']}",
            f"    Verbs for what the actor does: {dial['actorVerbs']}.",
            "    The actor counts as ONE of the discrete things above, not an addition to them.",
        ]
    else:
        brief.append(
            "  THE ACTOR: none. This is one of the few constellations with nobody in it — "
            "the materials and the place carry the image alone. Do not add a figure."
        )
    # Unconditional, appended to EVERY brief whatever the dial says, and placed
    # here rather than in STAGE2_SYSTEM because the brief is demonstrably the
    # part that gets obeyed (see the register-contribution requirement above,
    # moved here for the same reason). These are wallpapers: they sit behind a
    # desktop all day, and an enclosed room reads as claustrophobic at that size
    # however good its content is. Phrased as a countable share of frame and a
    # failure condition, matching the word-count rule, because "make it feel
    # open" is a mood word and mood words are not obeyed.
    # Unconditional, like OPENNESS below, and placed here for the same reason:
    # the brief is demonstrably the part that gets obeyed. Until now wonder was
    # only DESCRIBED, in the style charter, and nothing anywhere required it —
    # so a world of walking machines reliably produced a plausible flour mill.
    # A charter says what MAY exist; only the brief makes something appear.
    #
    # Phrased as a count and a failure condition, matching the openness and
    # word-count rules, because "make it wondrous" is a mood word and mood
    # words are not obeyed.
    # Style-specific rules, each phrased as a count and a failure condition
    # because mood words are not obeyed. Until 2026-09-26 THE IMPOSSIBLE and
    # OPENNESS were hardcoded here for every style; both were written for
    # ghibli's world, and cyberpunk is defined by different things (depth
    # rather than sky, neon, the tended). They now come from the style's
    # `[<style>.brief]` table -- see load_style_brief().
    for rule in (DEFAULT_STAGE2_BRIEF if style_brief is None else style_brief):
        brief.append("  " + rule.format_map(_BriefValues(dial)))
    if dial.get("spatial"):
        brief.append(f"  Where the event sits: {dial['spatial']}")
    if dial.get("light"):
        brief.append(f"  Light and concealment: {dial['light']}")
    user_lines += brief

    result, usage = _chat_json(stage2_model, stage2_system(), "\n".join(user_lines), "stage2", temperature=0.95, stage="stage2")
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

    # The dial has to exist BEFORE Stage 1.5, because it sets how many objects to
    # ask for and gets stored in that stage's cache entry.
    dial = dial_from_texture(reading["arc"].get("texture"))

    visual_signature, signature_cost = get_visual_signature(reading["natal"], config, stage2_model)
    stage1, stage1_cost = stage1_interpret(reading, stage1_model, period_key, config)
    # Registers are picked BEFORE Stage 1.5 as of 2026-09-26, because they are
    # now one of its inputs: the material half builds the objects and the
    # anomaly out of the primary register. Picked after, the register could
    # only reach Stage 2 as an adjective on a scene already cast without it.
    #
    # The "cached dial" workaround that used to follow this call is gone: it
    # kept a cached object list paired with the object count it was written
    # for, and objects are no longer cached.
    history = load_history()
    registers = load_registers()
    cliches = load_cliches()
    register, secondary_register = pick_registers(history, registers, dial["registerCohesion"])
    amplification, stage15_cost = stage15_amplify(
        stage1, stage1_model, period_key, config, dial=dial,
        style_key=style_key, style_world=load_style_world(style_key),
        register=register, secondary_register=secondary_register,
    )
    avoid = avoid_concepts(history)
    style_label, style_guidance = load_style_info(style_key)

    image_prompt, concept_tags, has_people, stage2_cost = stage2_image_prompt(
        stage1, visual_signature, register, avoid, cliches, stage2_model,
        style_label, style_guidance,
        secondary_register=secondary_register,
        amplification=amplification,
        dial=dial,
        # Primary only: the secondary is a supporting material. As of
        # 2026-09-20 registers.toml's [guidance] table is empty — its five
        # entries were inhabitant configurations and moved to actors.toml —
        # so this passes "" today and exists for the next register that
        # earns a steer.
        register_guidance=load_register_line(style_key, register),
        secondary_register_guidance=load_register_line(style_key, secondary_register),
        style_brief=load_style_brief(style_key),
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
        # "intrusion" is kept as an alias so build_review.py and every existing
        # pipeline-meta-*.json on disk keep rendering; "anomaly" is the real key.
        "anomaly": amplification.get("anomaly"),
        "anomalyType": amplification.get("anomalyType"),
        "intrusion": amplification.get("anomaly") or amplification.get("intrusion"),
        "affect": amplification.get("affect"),
        "register": register,
        "secondaryRegister": secondary_register,
        "conceptTags": concept_tags,
        "hasPeople": has_people,
        # Which model actually ran each stage. Previously only the COSTS were
        # recorded, so a review page could show what a stage cost but not what
        # produced it — and with the models now freely switchable from the
        # widget, a cost with no model attached is close to meaningless.
        "stage1Model": stage1_model,
        "stage15Model": stage1_model,  # Stage 1.5 deliberately runs on Stage 1's model
        "stage2Model": stage2_model,
        "signatureModel": stage2_model,
        "pipelineMode": dial["mode"],
        "texture": reading["arc"].get("texture"),
        "dial": {k: v for k, v in dial.items() if k != "mode"},
        "avoidedConcepts": avoid,
        "artStyle": style_key,
        # The display name for that key (styles.toml's own `label`), so
        # anything captioning a run — the "Themes Generated" entry's
        # title, the exported theme's README — can name the style the
        # way the Style dropdown does without re-reading the TOML.
        "artStyleLabel": style_label,
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
