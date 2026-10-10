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

All three stages need a real OpenAI API key (see astro-arc-apikey)
regardless of which backend (local SD or OpenAI) ends up rendering the
final image — Stages 1, 1.5 and 2 are themselves OpenAI chat completions.
Stage 1 and Stage 1.5 share the `stage1` key slot and model (both are
meaning-work); Stage 2 uses `stage2` (imagery-work).

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
READINGS_DIR = STATE_DIR / "readings"
AMPLIFICATIONS_DIR = STATE_DIR / "amplifications"

HISTORY_MAX = 30
# Narrowed from 14 to 8 on 2026-09-09. At 14 this was handing Stage 2 a
# 40-item "avoid all of this" list on every call — a very large negative-
# constraint load on a model that also has to honor two registers, a style
# world and a set of amplification objects. Anti-repetition
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
# Chat completion helper — every stage goes through this. The key is fetched fresh (via get_api_key(), a single
# secret-tool subprocess call) and used only inline in this one header dict;
# nothing here stores it, logs it, or returns it.
# ---------------------------------------------------------------------------

def _chat_json(model, system_prompt, user_prompt, slot, temperature=0.8, stage=None):
    """`slot` picks which of the three independently-stored API keys
    (astro-arc-apikey's stage1/stage2/image) authenticates this call —
    Stage 1 and Stage 2 use separate keys/models by design, since they're
    different tasks that may call for different accounts.

    `stage` names the PIPELINE stage (stage1/stage15/stage2/caption), which is
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
    # STYLE-OVERHAUL Prompt 2, 2026-10-10: the traditions this world's stories
    # come from. stage15_meaning keeps Jung and Neumann as the structural lens
    # and looks for the situation's form here (see MYTH_LENS).
    ("myth", "Myths and folk traditions its stories come from"),
    ("forbids", "Never in this world"),
)


# THE HIGHLIGHT, 2026-10-09. The one thing in a scene the eye goes to first.
# Until today this was "the anomaly", defined once for every style in Stage
# 1.5's and Stage 2's prompts -- wrong in exactly one way, far LARGER or far
# OLDER, never glowing, nothing explains it. That is ghibli's logic of the
# unremarked strange thing, and every other world inherited it (Garrett: the
# cyberpunk anomalies "aren't working"). Now each charter says what a highlight
# is in its world (`highlight`), and a register line may say what form it takes
# in that material. This text is the fallback for styles with no charter yet,
# so their behaviour does not change until they are chartered. The data key
# stays `anomaly` so archived records and sweeps keep reading.
#
# Rewritten 2026-10-10 (STYLE-OVERHAUL Prompt 1): it named "the size of a barn
# or a hillside" and "far older and more worn", and barn reached 21 of 33 days
# and ancient/older 37 of 52 highlights. No size comparison is named now, wear
# is the dial's condition, and "far older" gave way to a wrong place, so an
# unchartered style's highlight is not always an antique. A style's own
# highlight_modes, when it has them, say today's kind of wrongness.
DEFAULT_HIGHLIGHT = (
    "One thing, native to this world and made of its own materials, that is wrong in exactly "
    "one way: far too large for its kind, or somewhere its kind could never be. It is solid and "
    "lit like everything else; it does not glow, float, shimmer or look magical. Nothing in the "
    "scene explains why it is there. If it is large, choose a scale comparison native to this "
    "scene, never the same one twice, and put part of it in shot, close (one edge, the shadow it "
    "throws) rather than shrinking it or pushing it to the back of the frame."
)

HIGHLIGHT_MODES_LEN = (4, 6)


def load_style_highlight(style_key):
    """What a highlight is in this style's world: the charter's `highlight`
    field, or DEFAULT_HIGHLIGHT for a style with none."""
    world = (load_styles().get(style_key) or {}).get("world") or {}
    text = str(world.get("highlight") or "").strip() if isinstance(world, dict) else ""
    return text or DEFAULT_HIGHLIGHT


def load_highlight_modes(style_key):
    """`highlight_modes` under `[<style>.world]`: 4-6 kinds of wrongness, one
    drawn per run, or None for a style without them."""
    world = (load_styles().get(style_key) or {}).get("world") or {}
    values = world.get("highlight_modes") if isinstance(world, dict) else None
    if values is None:
        return None
    lo, hi = HIGHLIGHT_MODES_LEN
    values = [str(v).strip() for v in values if str(v).strip()] if isinstance(values, list) else []
    if not lo <= len(values) <= hi:
        raise PipelineError(f"styles.toml [{style_key}.world] highlight_modes has {len(values)} "
                            f"entries; it needs {lo} to {hi}")
    return values


def highlight_with_mode(highlight, dial):
    """The highlight definition with today's drawn kind of wrongness, if any."""
    mode = (dial or {}).get("highlightMode")
    return f"{highlight} Today's kind of wrongness: {mode}." if mode else highlight


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
    "and nothing in the scene treats it as remarkable; it may be the highlight. Not a decorated version of an ordinary "
    "thing: a machine that is clearly alive or awake, a creature far too large for its kind, a "
    "structure that walks or floats or grew where it stands, a being that is plainly not a "
    "person. Build it from this world's own materials, give it real weight, light it "
    "like everything else, and let the actor treat it the way they would a wall. An image "
    "in which everything could exist is a failed response.\n"
    "    What temper it is in: {wonderTone}\n"
    "    SCALE MUST READ. Put something of known human size next to it — a figure, a doorway, a "
    "cart, a gate — close enough in frame to measure it against. A huge thing photographed alone "
    "is just a texture; it only becomes huge beside something that is not.",
    "OPENNESS — applies to every image, whatever the numbers above say: at least half the frame must be open. Sky, horizon, landscape, sea, or depth that keeps going. Open means depth, not daylight: a night sky, a drop into dark or lit depths, or fog that keeps going all count, and how bright the open part is comes from the light line below, not from this rule. Pull the viewpoint back far enough that this is true. If the scene is indoors, the room must open onto that distance through a large window, a doorway, a missing wall or a broken roof, and what is seen through it must occupy a substantial part of the frame rather than being a bright rectangle in a corner. A fully enclosed interior is a failed response, and so is a close-up that crops the world out. Name what is out there rather than that it is far: not \"beyond\", \"in the distance\" or \"far off\".",
)


# STYLE-OVERHAUL Prompt 2: one thing in frame that could only exist in this
# world. A charter writes its own as `signature` in [<style>.brief]; ghibli's
# THE IMPOSSIBLE (`impossible`) already does this job. Everyone else gets this.
SIGNATURE_KEYS = ("signature", "impossible")
DEFAULT_SIGNATURE = ("SIGNATURE — at least one thing in frame could only exist in {styleLabel}'s "
                     "world, named explicitly. It may be the highlight or the actor. An image that "
                     "could be relabelled as another style is a failed response.")


def load_style_brief(style_key):
    """The rules Stage 2's brief carries for this style, in file order. A
    style with no `[<style>.brief]` table gets DEFAULT_STAGE2_BRIEF; every
    style without a rule keyed in SIGNATURE_KEYS also gets DEFAULT_SIGNATURE.
    (stage2_image_prompt still reads a None from an older caller as "use
    DEFAULT_STAGE2_BRIEF".)

    Rule text may use the dial's string values as {placeholders}
    ({wonderTone}, {light}, ...)."""
    brief = (load_styles().get(style_key) or {}).get("brief")
    if not isinstance(brief, dict):
        # Unchartered: ghibli's fallback rules, plus the default signature.
        return list(DEFAULT_STAGE2_BRIEF) + [_default_signature(style_key)]
    # A style with a [space] array gets today's drawn space line and the
    # WALLPAPER rule instead of its own fixed depth rule (see
    # draw_style_choices); keeping both would hand Stage 2 the old fixed
    # distance on top of the new one.
    skip = SPACE_REPLACES_BRIEF if load_style_space(style_key) else ()
    rules = [str(v).strip() for k, v in brief.items() if k not in skip and str(v).strip()]
    if not any(k in brief for k in SIGNATURE_KEYS):
        rules.append(_default_signature(style_key))
    return rules


def _default_signature(style_key):
    label = (load_styles().get(style_key) or {}).get("label") or style_key
    return DEFAULT_SIGNATURE.replace("{styleLabel}", label)


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


# Objects and highlights are drawn fresh every run, but nothing stopped the
# same ones coming back: the archive has one day's divination cup and votive
# liver in 12 runs across 4 styles. The last few runs' material is passed to
# the material call as spent, the way recent_constellations is to the meaning.
MATERIAL_AVOID_WINDOW = 6


def recent_material(history, window=MATERIAL_AVOID_WINDOW):
    """The objects and highlights of the last `window` runs of any style,
    most recent first, deduped. Entries from before 2026-10-10 carry neither
    and contribute nothing."""
    out = []
    for entry in history[:window]:
        for item in list(entry.get("objects") or []) + [entry.get("highlight")]:
            item = (item or "").strip() if isinstance(item, str) else ""
            if item and item not in out:
                out.append(item)
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
# The natal visual signature -- REMOVED 2026-10-08
#
# A paragraph of palette, light and contrast, generated once per natal chart
# and sent to Stage 2 on every generation of every style, meant to make the
# series read as "the same hand". Measured (verification/signature-ab):
# its words reached 0 of 36 Stage 2 prompts, and prompts with and without it
# differed no more than two draws WITH it (word overlap 0.44 vs 0.46 noise
# floor). The image model only sees Stage 2's prompt and the suffix, so it
# changed nothing -- while telling every brief "diffuse, softly scattered
# daylight, moderate contrast" against a dial that now owns light. Garrett
# chose removal over a redesign (Q60 a). The cached visual_signature.json is
# left on disk, unread. Continuity now comes from the reading, the dial and
# the style.
# ---------------------------------------------------------------------------


def _natal_hash(config):
    key = f"{config.get('birthDate','')}|{config.get('birthTime','')}|{config.get('latitude','')}|{config.get('longitude','')}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]



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


# A STAGE1_SYSTEM rewrite must invalidate readings cached under the old
# wording, or the fix silently does nothing for every period already
# generated. Bumped 2026-09-09 for the tone-floor rules above.
STAGE1_SCHEMA_VERSION = 3


def stage1_interpret(reading, stage1_model, period_key, config):
    """Cached per period_key, but keyed to the birth data too (see
    _natal_hash): regenerating the same day/week/month's image (e.g. while
    comparing style variants) shouldn't re-run Stage 1, but editing the
    birth date/time/location must invalidate the cache even within the same
    period, since Ascendant and transit-house placements feed straight into
    describe_natal()/describe_arc()'s prompt text below. Returns (result_dict,
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
# Since 2026-10-10 these are a FALLBACK for a style with no [verbs] table, and
# are drawn from three at a time like a charter's (draw_style_choices), so
# each list was lengthened to keep any one verb out of most prompts.
_VERBS_HARD = ("feeding, binding, eroding, crushing, spilling into, bearing down on, toppling, "
               "splitting, flooding, smothering, scorching, cracking open")
_VERBS_SOFT = ("holding, drying, ripening, settling, leaning against, warming, being mended, "
               "being carried, drifting, rising, cooling, swaying")

# How close the event sits — never whether the frame is open. The "hi" cell used
# to read "short sightlines, no far distance to escape into", which is a direct
# instruction to build an enclosed image, and on a high-intensity day it beat
# every other rule because the brief is the part that gets obeyed. Intensity now
# moves where the pressure sits INSIDE an open frame: close and pressing, or far
# and peripheral, with the distance visible either way. See the OPENNESS line in
# the composition brief for why that is invariant.
#
# {deep} since 2026-10-10 (STYLE-OVERHAUL Prompt 1): these and the site lines
# hard-coded "a far distance", and "far off / distance / beyond" was in 29 of
# 33 days' prompts in every style. stage2_image_prompt fills {deep} with the
# deepest layer today's space line provides, or of the frame for a style with
# no [space] array.
_SPATIAL_BY_INTENSITY = {
    "hi": "The event is close and large, pressing into the foreground — the viewer is inside it rather than observing it. The pressure comes from its scale and nearness, not from being shut in: the world still continues past it to {deep}.",
    "mid": "The event sits a few paces from the viewer, with one clear middle ground and {deep} behind it, real but untroubled.",
    "lo": "The event is small and peripheral — off to one side, or partly out of view. Most of the frame is world, not event.",
}

# How much of the frame the impossible occupies. Keyed on intensity, mirroring
# _SPATIAL_BY_INTENSITY, because the alternative — a constant level of wonder —
# is exactly the invariant the texture work exists to break. An image that is
# always astonishing stops being astonishing.
# How prominent the highlight is, by the day's intensity. Style-neutral on
# purpose (2026-10-09): the charter says WHAT the highlight is, this says only
# how much of the frame it takes. It replaces _WONDER_BY_INTENSITY, whose
# "ONE impossible thing... The impossible DOMINATES" was ghibli's vocabulary
# and reached only the styles that carried THE IMPOSSIBLE.
_PROMINENCE_BY_INTENSITY = {
    "lo": "Small and to one side: half-seen, or so settled into the place that it reads as part of it.",
    "mid": "Plainly present, at the scale of the larger things in the scene; part of the day's business rather than the event of it.",
    "hi": "It dominates: it sets the shape of the whole frame, and everything else, the actor included, is small against it.",
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
    "hard": "It is not charming. This world's strangeness turns dangerous, diseased or grieving: things that are sickened, possessed, boiling with something under the skin, maimed, enormous and enraged, or dying slowly and taking the place with them. Nobody is reassured by it and nobody is safe from it, and it is still ordinary enough that the day goes on around it.",
    # Its list of activities ("working, resting, feeding, travelling, or simply
    # long settled") went 2026-10-10: feed and settle were two of the most
    # repeated verbs in every style. The activity comes from the drawn verbs.
    "soft": "It is at peace, which is not the same as friendly. It is occupied with its own business and indifferent to being watched.",
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
# Until 2026-10-09 every cell also said "nothing explains why it is here". That
# is a property of ghibli's highlight, not of how an actor treats one, so it
# moved into ghibli's charter `highlight` and DEFAULT_HIGHLIGHT.
#
# STANCE ONLY since 2026-10-10 (STYLE-OVERHAUL Prompt 1), in all three actor
# tables. Each cell used to name actions ("prising at it, digging it out...",
# "cleaning, mending, feeding, oiling"), and those words reached every style's
# prompts verbatim; mended/patched turned up on 15 of 33 days across all seven
# styles. A cell now says only where the actor stands toward the thing (set
# against it, using it, at rest beside it, has not seen it), and the action
# comes from the drawn actor verbs. Each cell keeps its hard/soft and lo/mid/hi
# meaning; the soft/lo cell lost "weathered almost into landscape", because
# wear is now the dial's `condition`.
_ACTOR_ANOMALY_RELATION = {
    ("hard", "hi"): "It has the upper hand: it is in the actor's way and on top of what they are doing. They cannot move it and cannot ignore it.",
    ("hard", "mid"): "The actor is set against it, and is not winning yet.",
    ("hard", "lo"): "The actor has not seen it. It is behind them, above them, or under their feet, and it is not inert.",
    ("soft", "hi"): "The actor has put it to use, and is entirely unbothered by it.",
    ("soft", "mid"): "It is in the actor's care, as though it were theirs to look after.",
    ("soft", "lo"): "It has been here longer than the actor has. They live alongside it without a thought; it has become part of the place and stopped being remarkable to anyone.",
}

# NEW 2026-09-20, and the layer that most directly answers "museum objects
# strewn about". The old pipeline said what the objects WERE and never what
# anyone did with them, so they arrived as a list and were painted as a list.
# Keyed on the same (polarity, intensity) the rest of the dial uses, so no new
# axis is invented and no new astrology is needed.
_ACTOR_OBJECT_RELATION = {
    ("hard", "hi"): "The objects are in the actor's way or on top of them, more of them than anyone could manage.",
    ("hard", "mid"): "The actor is set against the objects and it is going badly: one is stuck, one is broken, one will not fit, and the effort shows in the body.",
    ("hard", "lo"): "The objects are not the actor's business. They belong to the place, and the actor's attention is elsewhere.",
    ("soft", "hi"): "The actor is using the objects for exactly what they are for, competently, with more of them to hand than are needed.",
    ("soft", "mid"): "The objects are in the actor's keeping, and in good order.",
    ("soft", "lo"): "The objects are at rest and so is the actor; nobody needs them now.",
}

# NEW 2026-09-20. Where the actor stands in relation to the PLACE, which is a
# different question from what they do with the things in it. Without this the
# actor tends to be dropped into the middle of the frame facing out.
_ACTOR_SITE_RELATION = {
    ("hard", "hi"): "The actor is caught in the site — down inside it, under it, hemmed by it, or on the part of it that is failing.",
    ("hard", "mid"): "The place is against the actor: its ground, its weather or its structure is resisting them.",
    ("hard", "lo"): "The actor is at the edge of the site, with most of the place still ahead of or behind them.",
    ("soft", "hi"): "The place is the actor's own: they know where everything is, and it answers them.",
    ("soft", "mid"): "The actor is somewhere they have been many times. Nothing about the place needs their attention.",
    ("soft", "lo"): "The actor is at rest in the site, and the place goes on around them without them.",
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
# Grouped by activity since 2026-10-10, like a charter's actor tables; the
# rest/play/ritual/travel groups are new.
_ACTOR_VERBS_HARD = {
    "work": "hauling, prising, bracing against, digging out, striking, shouldering, forcing",
    "rest": "sheltering under, waiting out, being pinned by",
    "ritual": "keeping vigil over, burning something at",
    "travel": "climbing, pushing through, being carried off by",
}
_ACTOR_VERBS_SOFT = {
    # "feeding" left this list 2026-10-10: it is also in _VERBS_HARD, so a
    # style on the fallback drew it twice as often as anything else (33%).
    "work": "tending, mending, sorting, washing, harvesting, carrying",
    "rest": "sleeping beside, eating, sitting with",
    "play": "playing, watching something at work, listening to",
    "travel": "walking past, riding, arriving at",
}

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
    "actorVerbs": _ACTOR_VERBS_SOFT["work"],
    "registerCohesion": "collide",
    "spatial": None,
    "light": None,
    "prominence": _PROMINENCE_BY_INTENSITY["mid"],
    "wonderTone": _WONDER_BY_POLARITY["soft"],
    "anomalyRelation": _ACTOR_ANOMALY_RELATION[("soft", "mid")],
    "objectRelation": _ACTOR_OBJECT_RELATION[("soft", "mid")],
    "siteRelation": _ACTOR_SITE_RELATION[("soft", "mid")],
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
        # Kept so the per-style draws (draw_style_choices) know which half of
        # each array to draw from without re-reading the texture.
        "polarity": polarity,
        "intensityBand": intensity_band,
        # For a style's own [light] lines (draw_style_choices).
        "exposureBand": exposure_band,
        "sites": sites,
        "objectRange": objects,
        "thingRange": things,
        "wordRange": words,
        "verbs": _VERBS_HARD if polarity == "hard" else _VERBS_SOFT,
        # Replaced by draw_style_choices with one drawn activity's verbs.
        "actorVerbs": (_ACTOR_VERBS_HARD if polarity == "hard" else _ACTOR_VERBS_SOFT)["work"],
        "registerCohesion": "collide" if polarity == "hard" else "cohere",
        "spatial": _SPATIAL_BY_INTENSITY[intensity_band],
        "prominence": _PROMINENCE_BY_INTENSITY[intensity_band],
        "wonderTone": _WONDER_BY_POLARITY["hard" if polarity == "hard" else "soft"],
        "light": _LIGHT_BY_EXPOSURE[exposure_band],
        "anomalyRelation": _ACTOR_ANOMALY_RELATION[(polarity, intensity_band)],
        "objectRelation": _ACTOR_OBJECT_RELATION[(polarity, intensity_band)],
        "siteRelation": _ACTOR_SITE_RELATION[(polarity, intensity_band)],
        "tonalDirection": (
            "This timeframe is under strain. Do not soften it."
            if polarity == "hard" else
            "This timeframe is not under strain. Do not import darkness it does not have."
        ),
    }


# ---- Per-style draws (STYLE-OVERHAUL Prompt 1, 2026-10-10) -----------------
#
# A review of 142 archived runs found the same verbs (spill 44, feed 36, bear
# down 28 ...), the same far-off distance, the same giant ancient worn highlight
# and the same vessels in every style, copied from fixed text every style
# receives. The fixed lists move into each charter as arrays, and each run
# draws a few entries, never ones this style drew in its last
# STYLE_RECENCY runs.
#
# Seeded from period_key + style + the history length, so a sweep (which keeps
# its own in-memory history) is reproducible, but a same-day regenerate, which
# comes after one more history entry, draws differently.

VERB_ARRAYS = ("process_hard", "process_soft", "actor_hard", "actor_soft")
VERB_ARRAY_LEN = (20, 30)
VERB_DRAW = 3
# The actor arrays are tables keyed by activity, and a run draws ONE activity
# first, then its verbs from that activity alone. Measured 2026-10-10: with one
# flat array of mixed activities, Stage 2 handed "polishing, splashing in,
# eating lunch on" picked polishing, and 83-92% of actors stayed at work.
# Drawing the activity gives work one share in four or five.
ACTIVITIES = ("work", "rest", "play", "ritual", "travel")
SPACE_ARRAY_LEN = (6, 8)
STYLE_RECENCY = 2


def _style_table(style_key, name):
    table = (load_styles().get(style_key) or {}).get(name)
    return table if table is not None else None


def load_style_verbs(style_key):
    """`[<style>.verbs]` as {array: [verb, ...]}, or None for a style without
    one -- which keeps the global _VERBS_* / _ACTOR_VERBS_* strings, so an
    unchartered style behaves as before. A table that is present must carry
    all four arrays at 20-30 entries each; anything else raises, because a
    half-written list would quietly hand Stage 2 three verbs from a dozen."""
    table = _style_table(style_key, "verbs")
    if table is None:
        return None
    if not isinstance(table, dict):
        raise PipelineError(f"styles.toml [{style_key}.verbs] must be a table")
    lo, hi = VERB_ARRAY_LEN
    out = {}
    for name in VERB_ARRAYS:
        raw = table.get(name) or []
        if name.startswith("actor_"):
            if not isinstance(raw, dict):
                raise PipelineError(f"styles.toml [{style_key}.verbs] {name} must be a table "
                                    f"keyed by activity ({', '.join(ACTIVITIES)})")
            groups = {k: [str(v).strip() for v in raw.get(k) or [] if str(v).strip()]
                      for k in ACTIVITIES}
            groups = {k: v for k, v in groups.items() if v}
            missing = [k for k in ("work", "rest", "travel") if k not in groups]
            if missing or not ({"play", "ritual"} & groups.keys()):
                raise PipelineError(f"styles.toml [{style_key}.verbs] {name} needs work, rest, "
                                    f"travel, and play or ritual")
            values = [v for k in groups for v in groups[k]]
        else:
            groups = None
            values = [str(v).strip() for v in raw if str(v).strip()]
        if not lo <= len(values) <= hi:
            raise PipelineError(f"styles.toml [{style_key}.verbs] {name} has {len(values)} "
                                f"entries; it needs {lo} to {hi}")
        if len(set(values)) != len(values):
            raise PipelineError(f"styles.toml [{style_key}.verbs] {name} repeats an entry")
        out[name] = groups or values
    return out


def _global_verbs():
    """The fallback for a style with no [verbs] table, in the same shape:
    the global lists, drawn from like any style's rather than sent whole
    (sent whole, 'drying' was in 67% of cosmic's prompts)."""
    split = lambda text: [v.strip() for v in text.split(",") if v.strip()]  # noqa: E731
    return {
        "process_hard": split(_VERBS_HARD),
        "process_soft": split(_VERBS_SOFT),
        "actor_hard": {k: split(v) for k, v in _ACTOR_VERBS_HARD.items()},
        "actor_soft": {k: split(v) for k, v in _ACTOR_VERBS_SOFT.items()},
    }


# How worn things are, drawn per run for every style (STYLE-OVERHAUL Prompt 1,
# Step 5). Wear used to be a constant -- the highlight, THE IMPOSSIBLE, the
# soft/lo actor relation and both charters all required it -- so every image
# came back weathered. Hard days lean to the far end, soft days to the near
# one; nothing is ruled out on either. A style may replace the list with a
# `conditions` array (a pristine world, a ruined one), drawn evenly.
CONDITIONS = ("newly made", "kept and cared for", "worn with long use", "failing", "ruined")
_CONDITION_WEIGHTS = {
    "hard": {"newly made": 1, "kept and cared for": 1, "worn with long use": 3, "failing": 3, "ruined": 3},
    "soft": {"newly made": 3, "kept and cared for": 3, "worn with long use": 3, "failing": 1, "ruined": 1},
}


# Measured 2026-10-10: on failing and ruined days the condition came back as the
# old surface words (worn, cracked, moss, patched), i.e. the same grainy texture.
CONDITION_HOW = ("Show it through the shape and state of things already named — what is "
                 "missing, fallen, new, kept or half-built — not through surface words like worn, "
                 "weathered, cracked, mossy or patched, and not with extra words of its own.")


def load_style_conditions(style_key):
    values = _style_table(style_key, "conditions")
    if not isinstance(values, list):
        return None
    values = [str(v).strip() for v in values if str(v).strip()]
    return values or None


# The brief rules a [space] array replaces, by their key in [<style>.brief].
SPACE_REPLACES_BRIEF = ("openness", "depth")

# Invariant, the same for every style with a [space] array: it keeps what
# OPENNESS and DEPTH were for (a wallpaper must not be claustrophobic) without
# fixing where the depth is.
WALLPAPER_RULE = ("WALLPAPER — The frame always has somewhere for the eye to go past the subject; "
                  "today's space line says where. A sealed close-up with nothing past the subject "
                  "is a failed response. Name what is there rather than that it is far: not "
                  "\"beyond\", \"in the distance\" or \"far off\".")


def load_style_space(style_key):
    """`[<style>.space]`: 6-8 one-sentence depth recipes, or None for a style
    without one, which keeps its OPENNESS or DEPTH brief rule."""
    values = _style_table(style_key, "space")
    if values is None:
        return None
    lo, hi = SPACE_ARRAY_LEN
    values = [str(v).strip() for v in values if str(v).strip()] if isinstance(values, list) else []
    if not lo <= len(values) <= hi:
        raise PipelineError(f"styles.toml [{style_key}.space] has {len(values)} recipes; it needs {lo} to {hi}")
    return values


def _style_history(history, style_key):
    return [e for e in history if e.get("artStyle") == style_key]


def _recent_draws(history, style_key, key, window=STYLE_RECENCY):
    """Everything this style drew under `key` in its last `window` runs."""
    used = set()
    for entry in _style_history(history, style_key)[:window]:
        value = (entry.get("draws") or {}).get(key)
        if isinstance(value, list):
            used.update(value)
        elif value:
            used.add(value)
    return used


def _style_rng(period_key, style_key, history):
    seed = hashlib.sha256(f"{period_key}|{style_key}|{len(history)}".encode()).hexdigest()
    return random.Random(int(seed[:16], 16))


def _draw(rng, pool, used, k):
    """k entries of `pool` not in `used`, falling back to the whole pool when
    fewer than k are left. File order in, so the seed alone decides."""
    fresh = [x for x in pool if x not in used]
    if len(fresh) < k:
        fresh = list(pool)
    return rng.sample(fresh, min(k, len(fresh)))


def draw_style_choices(style_key, dial, history, period_key):
    """This run's draws from the style's charter arrays, and the dial with them
    applied: (dial, draws). `draws` is what goes into the history entry (so the
    next run can exclude it) and into pipeline-meta (so the run page can show
    it). Keys a style has no array for are simply absent."""
    dial = dict(dial)
    draws = {}
    rng = _style_rng(period_key, style_key, history)
    hard = (dial.get("polarity") or "soft") == "hard"

    verbs = load_style_verbs(style_key) or _global_verbs()
    process = verbs["process_hard" if hard else "process_soft"]
    actor = verbs["actor_hard" if hard else "actor_soft"]
    draws["processVerbs"] = _draw(rng, process, _recent_draws(history, style_key, "processVerbs"), VERB_DRAW)
    # Activity first, then its verbs. No activity repeats until every other one
    # has had a turn: drawn independently, ghibli got work on 6 of 12 sweep
    # samples by chance (long-run share 21%), and a run of chores is exactly
    # what this exists to stop.
    last = _recent_draws(history, style_key, "activity", window=len(actor) - 1)
    draws["activity"] = _draw(rng, list(actor), last, 1)[0]
    draws["actorVerbs"] = _draw(rng, actor[draws["activity"]],
                                _recent_draws(history, style_key, "actorVerbs"), VERB_DRAW)
    dial["verbs"] = ", ".join(draws["processVerbs"])
    dial["actorVerbs"] = ", ".join(draws["actorVerbs"])
    dial["activity"] = draws["activity"]

    space = load_style_space(style_key)
    if space:
        draws["space"] = _draw(rng, space, _recent_draws(history, style_key, "space"), 1)[0]
        dial["space"] = draws["space"]

    # A style's own light lines, where the global ones assume an Earth sky
    # (STYLE-OVERHAUL Prompt 2). Not a draw: the exposure band decides.
    light = _style_table(style_key, "light")
    band = dial.get("exposureBand")
    if isinstance(light, dict) and band and str(light.get(band) or "").strip():
        dial["light"] = str(light[band]).strip()

    conditions = load_style_conditions(style_key) or list(CONDITIONS)
    used = _recent_draws(history, style_key, "condition")
    pool = [c for c in conditions if c not in used] or conditions
    weights = _CONDITION_WEIGHTS["hard" if hard else "soft"]
    draws["condition"] = rng.choices(pool, weights=[weights.get(c, 1) for c in pool])[0]
    dial["condition"] = draws["condition"]

    modes = load_highlight_modes(style_key)
    if modes:
        draws["highlightMode"] = _draw(rng, modes, _recent_draws(history, style_key, "highlightMode"), 1)[0]
        dial["highlightMode"] = draws["highlightMode"]

    return dial, draws


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
- From *The Great Mother*: the feminine as container — the elementary character that holds and will not release, against the transformative character that changes what it holds. It is one archetype among the others here and carries no more weight than they do; choose it only when the reading actually contains it.
- Alchemical operations where they genuinely fit, described at equal depth: nigredo (blackening, putrefaction, the matter rotting in the sealed vessel), albedo (the washing and whitening, what survives the rot coming clean), rubedo (the reddening, the work completed and embodied); the coniunctio, the prima materia, calcination, dissolution.

Rules:
- **Reach past the obvious archetype.** The separation of the World Parents, and the uroboros giving way to first separation, are the most generally applicable items in the vocabulary above: almost any reading about change, tension or emergence can be made to fit them, which is exactly what makes them the least informative choice. They are the satyr of this list. Measured over one real install's history, 83% of amplifications reached for the separation of the World Parents and only two distinct archetypes were ever used, out of the fifteen or so offered above. Choose it only when the reading's specific content makes it unmistakably right and nothing else fits, and treat anything under RECENTLY USED in the message below as already spent. Use the whole range: captivity is not dismemberment, petrification is not the night-sea journey, the found spring is not the feast, and nigredo is not albedo.
- Name the constellation honestly in whichever direction it actually runs, and the two directions get the same licence:
  - When it is dark, say so. Devouring, dissolution, dismemberment, petrification, suffocation and being buried alive are real archetypal contents. Do not soften them into "transition" or "growth."
  - When it is not dark, say that just as plainly. Abundance, the feast, ripening and harvest, the sacred marriage, the found spring, the debt forgiven, the long work finished, the body at rest and unguarded, the returning traveller recognised at the door are equally real archetypal contents — and they are not lesser, softer, or a failure to look hard enough. Do not import darkness the reading does not have. A reading of genuine ease that amplifies to suffocation is as wrong as a reading of genuine grief that amplifies to growth.
- You will be told which direction this timeframe actually runs in. Follow it.
- Never use "as if", "suggesting", "symbolizing", "evoking", "representing" or any other phrase that states what something means or compares it to something it is not. Name the thing. Whatever you write here is carried into the image prompt, and those words are forbidden there.
- **Use no visual or compositional language at all.** Do not say where anything sits, how it is lit, what color it is, what it looks like, or how any two things are arranged relative to each other. You are not staging a picture. If you begin composing, you have failed this task.
- **Name the actor the constellation already contains.** Every archetypal situation has a cast: the night-sea journey has someone inside the monster, the dragon-fight has whoever won it, the child born and held has a child and whoever holds it, captivity has a captive. Say who is in this situation, as a concrete living thing rather than a role label — "a ferryman's widow", "the oldest of the mill dogs", not "the seeker". Say WHO they are, not what they are doing: their activity in the picture is decided later, and an actor named mid-chore ("a mechanic tending the machine") stays at that chore in every image of the day. Then classify that actor as exactly one of the available kinds. Do not invent an actor the constellation does not imply, and do not reach for a person when the situation belongs to an animal, a swarm, or something that was alive and is not.
- **Classify the actor by WHAT IT IS, not by how many there are.** These are the kinds available, and one of them is exactly right:
{{ACTOR_KIND_TABLE}}
  `solitary` means an ORDINARY HUMAN BEING alone — nothing else. A single spirit is `presence`, not `solitary`. A single part-human figure is `man-beast`. A single creature is `animal`. A single machine working by itself is `automaton`, but two or more machines, creatures or spirits joined by one task are `figures-in-relation`, which is not restricted to people. A single corpse or remnant is `the-dead`. Being one of something is not what makes an actor `solitary`; being an unremarkable person is. This is the most common misclassification, so check it before you answer.
- **`none` is for the constellations that genuinely have nobody in them** — petrification, the sealed vessel, the tomb, the long aftermath. It is the exception, not the safe default. If you can name who is there, name them.

Respond with a JSON object with exactly these keys:
{"constellation": "one sentence naming the archetypal situation actually active, in the vocabulary above", "movement": "one short phrase naming what is moving into what — e.g. 'uroboric containment giving way to first separation', 'nigredo, the blackening not yet past', 'the dragon-fight won and the treasure not yet set down', 'albedo, the washing nearly through'", "affect": "3 to 6 words naming the felt bodily quality, not an emotion label — e.g. 'close, warm, faintly suffocating', 'dry, ringing, too bright', 'loose-limbed, cool, unhurried', 'wide open, steady, warm at the back of the neck'", "actor": "who is in this situation, as a concrete living thing rather than a role label, in a short phrase — or an empty string if the kind is 'none'", "actorKind": "exactly one of: {{ACTOR_KINDS}}"}"""

# The second half of Stage 1.5, split out 2026-09-26 -- see stage15_material.
STAGE15_MATERIAL_SYSTEM = """You are completing a Jungian amplification. The archetypal situation has already been named, and so has who is in it; you are given both, with the reading they came from. Your job is to name the MATERIAL of that myth — the beings, places, remains, workings and things the story contains — and one highlight, all built from the day's material register.

Rules:
- **The material you name is the FURNITURE OF THE MYTH already named, not objects standing beside it.** If the constellation is the night-sea journey through the belly of the monster, then the material is the monster's ribs arching overhead, the swallowed boat still upright inside it, the lamp that has stayed lit — NOT a thermos and a logbook belonging to someone who happens to be nearby. If it is the dragon-fight won, the material includes the dragon. Whatever the constellation put in the story, put it in the material. This is the single most important rule here: myth is the content, and a scene of ordinary possessions adjacent to a myth is a failure of this task.
- It need not be an object at all. Name **beings, creatures, places, remains, weather and workings** as freely as things: the thing that was swallowed, the thing that does the swallowing, the road that is also somebody, the wind that answers back. What matters is that each is concrete enough to picture and specific enough that it could only belong to this story.
- **It must belong to THE WORLD described below**, made of its materials and possible within its physics. The myth is the content; the world decides what the content is made of. A neon city and a river valley get the same monster and build it differently.
- **THE REGISTER decides what the material is made of.** You are given a primary material register, possibly a secondary one, and where available a description of how this world realises each. At least half of the pieces you name, and the highlight, must be made of the primary register's substance or belong to its territory; the secondary register may supply one or two. The myth decides what the pieces ARE; the register decides what they are MADE OF — a glacial day and a botanical day get the same monster, built once from ice and once from roots. A list made mostly of other materials is a failed response.
- Keep the specificity of a curator and drop the inventory of one. Not "an animal" but "the goat the toll-keeper sends across first"; not "a tool" but "the ferryman's pole, notched with the name of every crossing."
- Containers for liquid — troughs, cisterns, basins, bowls, jars, pools — are this pipeline's most repeated props. Name one only when the myth itself is about a vessel.
- NEVER return a generic category or a stock prop. A door, a mirror, a key, a candle, a chain, a scale, a mask, a book, a clock, a lantern, a rope, a bridge, a tapestry, a gear, a tree with visible roots — these and anything similarly available are failures. If an object could illustrate any reading whatsoever, it is the wrong object.
- **Vary everything the register leaves open.** Within the register's substance, reach across traditions, centuries and forms; whatever is not drawn from the register reaches across the material world — wax, lead, feather, grain, glass, salt, horn, silk.
- Never use "as if", "suggesting", "symbolizing", "evoking", "representing" or any other phrase that states what something means or compares it to something it is not. Name the thing. Whatever you write here is carried into the image prompt, and those words are forbidden there.
- **Use no visual or compositional language at all.** Do not say where anything sits, how it is lit, what color it is, what it looks like, or how any two things are arranged relative to each other. You are not staging a picture. If you begin composing, you have failed this task.
- **Name one highlight, as this world defines it.** It is not a decorated version of one of your pieces and not a label for the scene's mood; it is a thing, and it is the one the eye finds first.
- **The actor is already named and is not one of your pieces.** Do not restate it; name what belongs around it in this story.

Respond with a JSON object with exactly these keys:
{"objects": ["{{OBJECT_RANGE}} pieces of the myth itself \u2014 beings, creatures, places, remains, workings or things \u2014 each named concretely enough to picture, each unmistakably part of THIS story rather than generally suggestive of it, at least half of them made of the primary register"], "anomaly": "THE HIGHLIGHT: exactly one further presence, the thing in this scene the eye goes to first, named concretely enough to picture. What a highlight is in this world is given under THE HIGHLIGHT below; where the register's line says what form it takes in that material, follow it. It belongs to this story as much as the pieces do."}"""

# Bumped whenever STAGE15_SYSTEM changes in a way that should invalidate
# every cached amplification, or a prompt fix silently does nothing for
# every period already generated.
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
# 12->13 on 2026-10-10 (STYLE-OVERHAUL Prompt 1): the Great Mother paragraph
# lost its vessel inventory (bowl, oven, cave, tomb, well, granary...), the
# source the review traced the repeated containers back to.
# 13->14 on 2026-10-10: the actor is named as who, not mid-chore.
# 14->15 on 2026-10-10 (STYLE-OVERHAUL Prompt 2): a charter's `myth` field and
# MYTH_LENS reach the meaning call.
STAGE15_SCHEMA_VERSION = 15


# Sent to stage15_meaning only for a style whose charter has a `myth` field.
# Until then every world got the same Jungian stories rebuilt in different
# materials, so Ghibli rendered like Ghibli without becoming a Ghibli place.
MYTH_LENS = ("THIS WORLD'S MYTHS — Jung and Neumann above say what kind of situation this is. "
             "Find this situation's form in THIS WORLD's own traditions, listed under myth in "
             "the world below. Name the constellation in their terms where one fits.")


def style_has_myth(style_key):
    world = (load_styles().get(style_key) or {}).get("world") or {}
    return isinstance(world, dict) and bool(str(world.get("myth") or "").strip())


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
    not imagery-work.

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
    # The charter is an input as well as the style key: editing it must
    # re-draw that day's meaning, not serve one written for the old wording.
    charter_hash = hashlib.sha256(style_world.encode()).hexdigest()[:12]
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
                    and cached.get("charterHash") == charter_hash
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
        if style_has_myth(style_key):
            user_lines.append(MYTH_LENS)
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
    result["charterHash"] = charter_hash
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
                     style_world="", register=None, secondary_register=None,
                     avoid_material=None):
    """The MATERIAL half of Stage 1.5: objects and the highlight (key
    `anomaly`, see DEFAULT_HIGHLIGHT), built
    from the day's register. Never cached -- every generation, regenerates
    included, gets fresh material, which is the point of the split: the same
    meaning, a new register, new objects.

    Until 2026-09-26 the register was picked AFTER Stage 1.5 had already named
    every object, so it could only reach Stage 2 as a surface adjective: a
    20-register sweep came back as one scene (cyberpunk 20/20, ghibli 18/20).

    `meaning` is stage15_meaning's result. Its stored tonalDirection is used
    rather than the current dial's, so the material runs in the same direction
    as the meaning it was written for even when the Moon has moved since.

    `avoid_material` is recent_material()'s list: the last runs' objects and
    highlights, passed as spent. None or [] sends nothing.

    Returns (result_dict, cost)."""
    if dial is None:
        dial = dict(NO_TEXTURE_DIAL)
    object_lo, object_hi = dial["objectRange"]
    system_prompt = (
        STAGE15_MATERIAL_SYSTEM
        .replace("{{OBJECT_RANGE}}", f"{object_lo} to {object_hi}")
    )

    user_lines = []
    if style_world:
        user_lines.append(
            "THE WORLD — every piece you name, and the highlight, must belong to it:\n"
            + style_world
        )
    if register:
        line = load_register_line(style_key, register)
        user_lines.append(f"PRIMARY REGISTER — at least half the pieces, and the highlight, are made of this: {register}"
                          + (f"\n  In this world: {line}" if line else ""))
    if secondary_register:
        line = load_register_line(style_key, secondary_register)
        user_lines.append(f"SECONDARY REGISTER — may supply one or two pieces: {secondary_register}"
                          + (f"\n  In this world: {line}" if line else ""))
    user_lines.append("THE HIGHLIGHT — what it is in this world: "
                      + highlight_with_mode(load_style_highlight(style_key), dial))
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
    # The same line Stage 2 gets, so the pieces named here do not arrive
    # already weathered on a day whose things are newly made.
    if dial.get("condition"):
        user_lines.append(f"Condition of things in this scene: {dial['condition']}. {CONDITION_HOW}")
    if avoid_material:
        user_lines.append(
            "RECENTLY USED — these pieces and highlights are spent; do not reuse these or close variants:\n"
            + "\n".join(f"  - {m}" for m in avoid_material)
        )

    result, usage = _chat_json(model, system_prompt, "\n".join(user_lines), "stage1",
                               temperature=0.95, stage="stage15")
    for key in ("objects", "anomaly"):
        if not result.get(key):
            raise PipelineError(f"Stage 1.5 material response missing '{key}': {result!r}")
    if not isinstance(result["objects"], list):
        raise PipelineError(f"Stage 1.5 'objects' is not a list: {result['objects']!r}")
    return {
        "objects": result["objects"],
        "anomaly": result["anomaly"],
        # Kept as a key, always empty, so archived records and harnesses that
        # read it keep working. The scale/time typing went with the universal
        # anomaly definition on 2026-10-09.
        "anomalyType": None,
        "register": register,
        "secondaryRegister": secondary_register,
        "materialDial": {k: v for k, v in dial.items() if k in ("objectRange",)},
    }, chat_call_cost(model, usage)


def stage15_amplify(stage1_result, model, period_key, config, cache=True, dial=None,
                    avoid_archetypes=None, avoid_kinds=None, style_key=None,
                    style_world="", register=None, secondary_register=None, material=True,
                    avoid_material=None):
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
        style_world=style_world, register=register, secondary_register=secondary_register,
        avoid_material=avoid_material)
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
STAGE2_SYSTEM = """You are composing a single dense image to communicate a specific psychological meaning to a viewer who knows nothing about astrology. You are given a psychological reading, its one-line distillation, and — most importantly — a set of amplification material: an archetypal constellation, a handful of specific objects, and one highlight. Your job is to BUILD A SCENE OUT OF THAT MATERIAL. You are not inventing a symbol from scratch; the symbols have already been found for you. Compose them.

WHAT THE IMAGE MUST CONTAIN

- Length. THE COMPOSITION BRIEF in the user message gives you a word range for this specific image. Stay inside it, and never exceed 140 words under any circumstances. Count them before you answer. Density comes from what you name, not from how long the sentences are. The range is smaller on some days than others and that is deliberate: a scene with few things in it gets a SHORT prompt. Do not spend a leftover word budget on what the image means.
- Density. THE COMPOSITION BRIEF gives you, for this image only, how many discrete nameable things to include, how many separate sites they occupy, and where the event sits relative to the viewer. Obey those numbers exactly. They are not the same every time and must not be normalised toward what you would usually do.
- When the brief asks for FEW things, that is a correct image and not a sparse one — but "few things" must never become "empty". Fill the frame with the WORLD instead of with more objects, as the style you are given describes it. A low count means fewer separate subjects competing, not less to look at. Padding a quiet scene with extra objects defeats the entire point, and so does handing back a single subject on a plain field.
- Use most of the amplification objects you were given. Put them in the scene as real physical things, at different depths and different scales. You may add a small number of connective things the scene needs to hold together, but the given objects are the substance.
- THE HIGHLIGHT IS MANDATORY. Place it in the scene as a real part of it — the thing the eye goes to first. What a highlight is in this world, and how prominent it is in this image, are in THE COMPOSITION BRIEF; follow both.
- How the actor treats the highlight varies per image and THE COMPOSITION BRIEF tells you which it is. Follow that exactly. Do not write it as a separate closing clause tacked onto the end of the prompt, and do not flag it with "incongruously", "unmistakably", "matter-of-fact", "out of place", "unremarked" or "unnoticed" — naming the wrongness out loud is what made it read as a bolted-on item rather than a fact of the scene. State it as plainly as you state the wall or the weather, wherever it belongs in the description.
- Interaction, not arrangement. Things must be doing something to each other, and THE COMPOSITION BRIEF gives you the verbs that apply to this image — use that register of action, not a generically forceful one. A still-life of symbols placed side by side is a failure. Say what is happening between things, not just what is present.
- Material registers. THE AMPLIFICATION OBJECTS ARE WHAT IS IN THE SCENE; THE REGISTERS ARE WHAT THE SCENE IS MADE OF. The primary register supplies the setting and the substance the scene is built from. Where a secondary register is given it must also be physically present as real matter, not as a mood, a color or a passing mention, and each register must be visible as substance in the frame: through the objects where they are made of it, and otherwise through one further named thing of its own. Test yourself: if this scene would read exactly the same with both registers swapped for two others, you have ignored them and must rebuild it. A register is the real substance it names, present as matter in the frame, never only a mood, a colour or a passing mention.
- THE COMPOSITION BRIEF tells you whether the two registers COLLIDE or COHERE in this image. Colliding means the friction between two unrelated materials is part of what makes the image worth looking at — feathers against wire, glass against bread. Cohering means they belong to one material family and should read as one continuous world, not as two things meeting; build a place that is all of a piece. Where no secondary register is given at all, let the primary carry the whole scene and lean on the rendering style's world for everything else.

HOW TO WRITE IT

- Describe only what is physically there. NEVER explain what anything means. The following words and every close relative of them are forbidden in your output: symbolizing, symbolic, suggesting, suggestive, representing, as if, evoking, conveying, inviting, embodying, reflecting (in the figurative sense), hinting, capturing, "a sense of", "the sense of", "a feeling of", "the feeling of", "an impression of", "creating an atmosphere of", "limned with", "the whole scene is", "there is a quality of". Any article works the same way — "the sense of renewal" is forbidden exactly as "a sense of renewal" is. An image model cannot paint a verb about meaning; every one of those words spends your budget to produce nothing. Say the thing; let it mean what it means.
- Concrete and oddly specific over generically mystical. Name particular objects, particular marks, particular substances. "A goat with one sawn-off horn" beats "a creature of burden."
- Avoid generic intensifiers. "Vibrant," "intricate," "swirling," "mystical," "ethereal," "magical" are close to meaningless to an image model and are this pipeline's known crutch words. Prefer a described fact over an intensifier.
- Match the light, scale and spatial pressure to the reading's actual feeling before any symbol does. When the reading is dark, the image is genuinely dark — close, heavy, dim, cold, airless, or too exposed — not a pleasant scene with a sad object in it. When the reading is light, let it be genuinely light. Do not default to warm golden pleasantness; that is this pipeline's known failure mode.

PEOPLE

- THE ACTOR IS GIVEN TO YOU, and it is not optional. THE COMPOSITION BRIEF names who or what is in this scene — a person, a crowd, an animal, a swarm, a part-human figure, a machine that has not stopped, a body — along with how that actor is framed. Put them in the frame and give them something to do. The register says what the world is MADE OF; the actor says WHO IS IN IT. They are independent: a mechanical world with one person in it is a normal image, not a contradiction.
- The only exception is an actor of "none", which THE COMPOSITION BRIEF will state explicitly. Then the materials carry the image alone, as the place and its objects.
- INTERACTION WITH THE ACTOR IS THE POINT. A scene where the actor merely stands among the objects is the same failure as a still-life. The brief gives you the actor's relation to the anomaly, to the objects and to the site: use all three. What the actor does comes from the actor verbs in THE COMPOSITION BRIEF; take the action from those rather than from your own habits. Meaning comes from what passes between the actor and the world, not from their sharing a frame.
- Figures act; they do not emote. Give each figure an activity and a thing to attend to, and let the activity explain the posture. The activity is whatever the brief's actor verbs call for: work, but just as often rest, play, ritual or travel. Not every figure is doing a chore. Faces are fine: let them show the effort or attention of the activity, and no expression that would still read if the activity were taken away. Never a pose that exists to be read as a feeling, and never a figure whose job is to react to another figure or to the scene. Two or more figures may share a frame in a shared activity or a physical struggle: show what their bodies are doing — grip, weight, strain — never why they do it, and never which of them to side with. Physical, never graphic: no wounds, blood or gore. This holds for people, animals and machines alike.
- The actor is not a way to reach the density requirement, and neither are people. A crowd or a swarm counts as ONE element no matter how many bodies are in it. Reach the thing count with objects, structures and materials, never by multiplying faces.

STYLE

- You are told which rendering style this image will be painted in, along with its world: what it can depict and what lives in it. Treat that description as a source of invention, not just a limit: reach into that world and invent imagery that belongs there. Where it marks a boundary (what the style cannot render), it still applies as one: a grounded style cannot credibly render a being made of pure energy; find a concrete equivalent inside its vocabulary.
- The division of labour, and both halves must be visible in the prompt: THE REGISTERS decide what things are MADE OF. THE WORLD decides WHAT PLACE this is and WHAT KIND OF THING lives here: its buildings, its machines, its creatures, its people and their customs. A geological day in a world of walking machines is that world's quarry, worked by that world's machines and spirits, cut from stone.
- The world must not turn every register into the same favourite scene: if the place would be the same whatever the registers were, rebuild it.

COLOR, REGISTERS, AVOIDANCE

Let each scene's own materials decide its colors, the light line in THE COMPOSITION BRIEF decide its light, and the brief decide its shape. Do not reach for the same palette from image to image — naming one palette every time is what made this series monotonous.

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


# The judge model the calibration set was scored on (score_melodrama.py's
# default); `captionModel` in the config overrides it. Two resamples before
# giving up: enough to escape an unlucky draw, not enough to loop on a reading
# whose honest content Stage 2 keeps rendering the same way.
CAPTION_MODEL = "gpt-4o-mini"
CAPTION_RESAMPLES = 2


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


def stage2_image_prompt(stage1_result, register, avoid_tags, cliches, stage2_model,
                         style_label=None, style_guidance=None, secondary_register=None,
                         amplification=None, dial=None, register_guidance=None,
                         style_brief=None, secondary_register_guidance=None,
                         highlight=None):
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
            f"  HIGHLIGHT (must appear): {amplification.get('anomaly') or amplification.get('intrusion', '')}",
            f"  Felt quality: {amplification.get('affect', '')}",
            "",
        ]


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
    space = dial.get("space")
    deep = ("the deepest layer today's space line provides" if space
            else "the deepest layer of the frame")
    site_text = {
        1: f"ONE site. A single place, one thing happening in it. Do not split the frame into near/middle/far stations with a separate event at each — the single place still opens onto {deep}, it just does not stage a second scene out there.",
        2: f"TWO sites — one near, one at {deep} — and nothing in between competing with them.",
        3: f"THREE depths: something close to the viewer, a situation in the middle ground, and {deep}.",
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
        # Was "...beyond the objects you were given" until 2026-10-10. Stage 1.5
        # has built at least half the objects from the primary register since
        # 2026-09-26, so the extra thing was usually a duplicate, and its
        # examples ("aquatic" -> "real water") were among the repeats.
        f"  REGISTER: the objects you were given already carry the primary register ({register}). Name one further thing made of it only if no object in the scene is plainly made of it.",
        f"  What a highlight is in this world: {highlight_with_mode(highlight or DEFAULT_HIGHLIGHT, dial)}",
        f"  How prominent the highlight is in this image: {dial.get('prominence') or _PROMINENCE_BY_INTENSITY['mid']}",
        f"  The highlight, and how the actor treats it: {dial['anomalyRelation']}",
    ]
    # The "THE ANOMALY IS ENORMOUS, put PART of it in shot" rule that sat here
    # (fired only for scale anomalies) moved into DEFAULT_HIGHLIGHT and ghibli's
    # charter `highlight` on 2026-10-09, with the scale/time typing it keyed on.
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
            (f"    What the actor is doing — {dial['activity']}: {dial['actorVerbs']}. "
             f"This is the actor's activity in this image, whatever their trade. The lines "
             f"above say where the actor stands toward things; this line says what they are "
             f"doing, and where the two pull apart, this line wins."
             if dial.get("activity") else
             f"    Verbs for what the actor does: {dial['actorVerbs']}."),
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
    if space:
        brief += [f"  SPACE — today's depth: {space.rstrip('.')}.", f"  {WALLPAPER_RULE}"]
    if dial.get("condition"):
        brief.append(f"  Condition of things in this scene: {dial['condition']}. {CONDITION_HOW}")
    if dial.get("spatial"):
        brief.append(f"  Where the event sits: {dial['spatial'].replace('{deep}', deep)}")
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
    # Per-style draws (verbs, ...) from the charter's arrays, never repeating
    # what this style drew in its last STYLE_RECENCY runs. Before Stage 1.5,
    # because the dial they change is one of its inputs.
    dial, draws = draw_style_choices(style_key, dial, history, period_key)
    register, secondary_register = pick_registers(history, registers, dial["registerCohesion"])
    amplification, stage15_cost = stage15_amplify(
        stage1, stage1_model, period_key, config, dial=dial,
        style_key=style_key, style_world=load_style_world(style_key),
        register=register, secondary_register=secondary_register,
        avoid_material=recent_material(history),
    )
    avoid = avoid_concepts(history)
    style_label, style_guidance = load_style_info(style_key)

    # The melodrama rule, enforced before the image call as B4 specified. It was
    # built and calibrated on 2026-09-20 (21/21 against Garrett's labels) but
    # never called from here until 2026-09-28, so no generation was checked.
    # A failed check resamples Stage 2, which costs a chat call rather than a
    # render. If every attempt fails, the last prompt ships anyway and the
    # failure is recorded: a strict judge must never cost the day its image.
    # passed=None (the judge errored) counts as "not checked", per
    # caption_check's own contract.
    caption_model = config.get("captionModel") or CAPTION_MODEL
    caption_attempts = []
    stage2_cost = 0.0
    for _attempt in range(1 + CAPTION_RESAMPLES):
        image_prompt, concept_tags, has_people, attempt_cost = stage2_image_prompt(
            stage1, register, avoid, cliches, stage2_model,
            style_label, style_guidance,
            secondary_register=secondary_register,
            amplification=amplification,
            dial=dial,
            register_guidance=load_register_line(style_key, register),
            secondary_register_guidance=load_register_line(style_key, secondary_register),
            style_brief=load_style_brief(style_key),
            highlight=load_style_highlight(style_key),
        )
        stage2_cost += attempt_cost or 0.0
        caption, hits, passed = caption_check(image_prompt, caption_model)
        caption_attempts.append({"caption": caption, "hits": hits, "passed": passed})
        if passed is not False:
            break

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
        # The style and its draws, so the next run of this style can exclude
        # what this one drew (see draw_style_choices).
        "artStyle": style_key,
        "draws": draws,
        # For recent_material: the next runs' material call treats these as spent.
        "objects": amplification.get("objects") or [],
        "highlight": amplification.get("anomaly") or "",
    }
    # NOT saved here as of 2026-10-08. build() runs before the image call, so
    # saving the entry now meant a render refused by the provider still counted
    # as a generation: the register rotation and the avoided-concepts window
    # both moved on for a day that produced no image. The entry rides in the
    # metadata instead, and astro-arc-generate commits it with
    # `llm_pipeline.py --commit-history` once the image exists.

    metadata = {
        "reading": stage1["reading"],
        "distillation": stage1["distillation"],
        "narrativePosition": stage1["narrativePosition"],
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
        "historyEntry": entry,
        # One row per Stage 2 attempt; more than one means the melodrama check
        # caught something and Stage 2 was resampled.
        "captionCheck": {"model": caption_model, "attempts": caption_attempts,
                         "passed": caption_attempts[-1]["passed"]},
        # Which model actually ran each stage. Previously only the COSTS were
        # recorded, so a review page could show what a stage cost but not what
        # produced it — and with the models now freely switchable from the
        # widget, a cost with no model attached is close to meaningless.
        "stage1Model": stage1_model,
        "stage15Model": stage1_model,  # Stage 1.5 deliberately runs on Stage 1's model
        "stage2Model": stage2_model,
        "pipelineMode": dial["mode"],
        "texture": reading["arc"].get("texture"),
        "dial": {k: v for k, v in dial.items() if k != "mode"},
        "draws": draws,
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
        # "cached, no call made" (stage1/stage1.5) or "model not
        # in cost_estimate.py's rate table" (unknown, not zero).
        "stage1Cost": stage1_cost,
        "stage15Cost": stage15_cost,
        "stage2Cost": stage2_cost,
    }
    return final_prompt, metadata


def commit_history(metadata_path):
    """Append the history entry build() left in a run's metadata. Called by
    astro-arc-generate only after the image has rendered, so a refused or
    failed render never advances the rotation. A metadata file with no entry
    (written before 2026-10-08) is a no-op rather than an error."""
    metadata = json.loads(Path(metadata_path).read_text())
    entry = metadata.get("historyEntry")
    if entry:
        save_history_entry(entry, load_history())


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--commit-history":
        commit_history(sys.argv[2])
        return
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
