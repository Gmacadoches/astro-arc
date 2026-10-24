#!/usr/bin/env python3
"""Astro-Arc's three-stage LLM prompt pipeline — replaces the old
symbol_map.py lookup-table approach. Nothing in here builds imagery from a
fixed vocabulary anymore; the tables that remain (registers.toml,
cliches.toml, styles/<key>.toml) are tuning constraints on an LLM, not content
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
import string
import time
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from openai_image_gen import ApiKeyError, get_api_key  # noqa: E402
from cost_estimate import chat_call_cost  # noqa: E402
from http_limits import ResponseTooLarge, open_no_redirect, read_error_capped, read_json_capped  # noqa: E402
from paths import CONFIG_FILE, USER_STYLES_DIR  # noqa: E402
from pipeline_state import PipelineState  # noqa: E402

import urllib.error
import urllib.request

# Ships beside the code — backend/pipeline, one directory up from backend/bin —
# so the tables travel with the checkout and need no install step.
PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
# Narrowed from 14 to 8 on 2026-09-09. At 14 this was handing Stage 2 a
# 40-item "avoid all of this" list on every call — a very large negative-
# constraint load on a model that also has to honor two registers, a style
# world and a set of amplification objects. Anti-repetition
# now has positive pressure from two colliding registers plus per-cycle
# amplification material, so it no longer has to carry variety on its own.
AVOID_WINDOW = 8
COLD_START_THRESHOLD = 10
REGISTER_LOOKBACK_DAYS = 5

# Bounded retry for the chat API — see openai_chat. 5 attempts with the wait the
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
#
# This is the OpenAI adapter at the chat seam. Every stage takes `chat=`, any
# callable with openai_chat's signature returning (json_dict, usage), and falls
# back to this one when it is None. fallback_audit.py passes a stub there, so a
# prompts-only run makes no call and writes no usage or capability state.
# ---------------------------------------------------------------------------

def openai_chat(model, system_prompt, user_prompt, slot, temperature=0.8, stage=None):
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
            # peer; the cap covers one that keeps sending; redirects are
            # refused unread, since following one drains it uncapped.
            with open_no_redirect(req, timeout=60) as resp:
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
# The catalog — registers, actors, cliches and the themes are hand-edited TOML,
# read once at the start of a run (Catalog.load) so an edit takes effect on the
# next run with no restart. Every choice in a composition is made from one
# catalog: build() loads it, a harness loads its own and passes it in.
# ---------------------------------------------------------------------------

# One file per theme (decision 4): styles/<key>.toml plus a required
# styles/<key>.jpg preview. The key is the file name. Shipped themes sit beside
# the code; a user's own go in USER_STYLES_DIR.
SHIPPED_STYLES_DIR = PIPELINE_DIR / "styles"
DEFAULT_STYLE = "symbolist"

_NOTICES = set()


def _notice(line):
    """A loader warning, printed once per process however many catalogs a
    process loads."""
    if line not in _NOTICES:
        _NOTICES.add(line)
        print(line, file=sys.stderr)


def _read_register_families(path):
    """{family: [register, ...]} straight from registers.toml.

    That file is the only place any of this is written down — see its header.
    Everything else (the flat rotation list, the family lookup) is derived
    from it, so adding a register is one edit in one file and cannot
    half-land. Who appears in the image is actors.toml's job, not this
    file's — see _read_actors()."""
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            families = tomllib.load(f).get("families", {})
    except (tomllib.TOMLDecodeError, OSError):
        return {}
    return {fam: [r for r in regs if isinstance(r, str) and r.strip()]
            for fam, regs in families.items() if isinstance(regs, list)}


def _read_actors(path):
    """actors.toml as {"kinds": {...}, "exclude": {...}}, or empty on a
    missing or malformed file."""
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        print(f"llm_pipeline: actors.toml unreadable ({exc}); no actor will be set",
              file=sys.stderr)
        return {}


def _read_cliches(path):
    if not path.exists():
        return []
    with open(path, "rb") as f:
        return tomllib.load(f).get("terms", [])


def _read_theme(path, source, catalog):
    try:
        with open(path, "rb") as f:
            return Theme(path.stem, tomllib.load(f), source, path, catalog)
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as e:
        return Theme(path.stem, {}, source, path, catalog, parse_error=str(e))


class Catalog:
    """The themes, registers, actor kinds and cliches as read at one moment.

    Load one per run with Catalog.load() and pass it (or a theme from it)
    down; nothing reads the files behind its back, so a run sees one
    consistent set however long it takes. A script that writes a theme file
    loads a new catalog to see it."""

    def __init__(self, styles=None, register_families=None, actors=None, cliches=None):
        self.styles = dict(styles or {})
        self.register_families = dict(register_families or {})
        self._actors = actors or {}
        self.cliches = list(cliches or [])
        self._verdicts = {}
        self._warned = set()

    @classmethod
    def load(cls, shipped_dir=SHIPPED_STYLES_DIR, user_dir=USER_STYLES_DIR, drafts=(),
             pipeline_dir=PIPELINE_DIR):
        """Every theme by key: the shipped ones, then the user's, then
        `drafts` — theme files outside both folders (astro-arc-style's draft,
        which may be written anywhere), each taking the place of a user theme
        with its key.

        A user file or draft whose key is a shipped theme's is not loaded: a
        shipped key always means the shipped theme. A user file is ignored
        with a warning; a draft raises ValueError, since its caller asked for
        it by name. A file that does not parse still loads, empty, so it can
        be listed as failing validation."""
        shipped_dir, user_dir = Path(shipped_dir), Path(user_dir)
        catalog = cls(register_families=_read_register_families(pipeline_dir / "registers.toml"),
                      actors=_read_actors(pipeline_dir / "actors.toml"),
                      cliches=_read_cliches(pipeline_dir / "cliches.toml"))
        if not catalog.register_families:
            _notice("llm_pipeline: warning — registers.toml has no [families] table; "
                    "the register rotation will be empty")
        themes = {path.stem: _read_theme(path, "shipped", catalog)
                  for path in sorted(shipped_dir.glob("*.toml"))}
        try:
            user_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        for path in sorted(user_dir.glob("*.toml")):
            if path.stem in themes:
                _notice(f"astro-arc: warning: user theme {path} has the key of a shipped theme "
                        f"('{path.stem}') and is ignored; rename the file to use it")
                continue
            themes[path.stem] = _read_theme(path, "user", catalog)
        for path in drafts:
            path = Path(path).resolve()
            if path.parent == shipped_dir.resolve():
                continue  # a shipped theme, already loaded as itself
            if (shipped_dir / f"{path.stem}.toml").exists():
                raise ValueError(f"'{path.stem}' is the key of a shipped theme; rename {path.name}")
            source = "user" if path.parent == user_dir.resolve() else "draft"
            themes[path.stem] = _read_theme(path, source, catalog)
        catalog.styles = themes
        return catalog

    # ---- themes ----------------------------------------------------------

    def style(self, key):
        """The theme `key`; KeyError if there is none. `key` is resolved."""
        return self.styles[key]

    def resolve(self, key):
        """The configured `artStyle`, resolved once when a run reads the
        config. A key with no theme file (stale, deleted, or a typo) falls back
        to DEFAULT_STYLE with a warning rather than failing the day's image;
        this is the only place an unknown key is forgiven, and everything else
        takes a resolved key."""
        if key and key in self.styles:
            return key
        if key:
            print(f"astro-arc: warning: {missing_style_warning(key)}", file=sys.stderr)
        return DEFAULT_STYLE

    def validate(self, key):
        """(errors, warnings) for one theme, worked out once per catalog: see
        _validate_charter, which reads the other themes and the registers too."""
        if key not in self._verdicts:
            self._verdicts[key] = _validate_charter(self, key)
        return self._verdicts[key]

    def require(self, key):
        """validate for a run: print the warnings (the first time this catalog
        is asked), refuse on any error."""
        errors, warnings = self.validate(key)
        if key not in self._warned:
            self._warned.add(key)
            for line in warnings:
                print(f"astro-arc: style warning: {line}", file=sys.stderr)
        if errors:
            raise PipelineError(f"Style '{key}' has an incomplete charter and cannot be used "
                                "for a run:\n  " + "\n  ".join(errors))

    # ---- registers -------------------------------------------------------

    @property
    def registers(self):
        """Every register, in file order — which is the order a cold-start
        install cycles through before the recency rotation takes over."""
        return [r for regs in self.register_families.values() for r in regs]

    def register_family(self, register):
        """Which material family a register belongs to, or None if the file
        does not place it. Two registers with family None would cohere with
        each other, so `None` is a real answer callers have to handle, not a
        default."""
        for fam, regs in self.register_families.items():
            if register in regs:
                return fam
        return None

    # ---- actors ----------------------------------------------------------

    @property
    def actor_kinds(self):
        """Every actor kind, in file order — which is the order a cold-start
        install cycles through before the recency rotation takes over, exactly
        as the registers do."""
        return list((self._actors.get("kinds") or {}).keys())

    def actor_guidance(self, kind):
        """How this kind is framed, or "" for `none` and for an unknown kind.

        `none` is a real kind with an empty body: a purely material image is a
        legitimate outcome, just no longer the default it was on ~88% of
        generations.
        """
        return ((self._actors.get("kinds") or {}).get(kind) or "").strip()

    def actor_kind_summary(self, kind):
        """The first sentence of a kind's guidance — enough for Stage 1.5 to
        CHOOSE between kinds, without spending the whole paragraph Stage 2
        needs in order to RENDER one.

        Stage 1.5 used to receive a bare list of names. `solitary` reads as
        itself and `presence` does not, so a spirit with a body and a trade was
        consistently typed `solitary`, and the guidance attached to the kind it
        should have had never fired. The descriptions already existed in
        actors.toml and only the stage that renders could see them.
        """
        text = self.actor_guidance(kind)
        if not text:
            return ""
        return text.split(". ")[0].strip().rstrip(".")

    def actor_excluded_for(self, register):
        """Actor kinds that collapse into the substance when paired with this
        register — `automaton` with `mechanical` makes the actor out of the
        same material as the world. From actors.toml's [exclude] table, so the
        pairing rule lives beside the kinds rather than in code."""
        return set((self._actors.get("exclude") or {}).get(register, []))


def missing_style_warning(key):
    """What a run and the panel both say about a configured key with no theme."""
    return f"art style '{key}' is not an installed theme; using '{DEFAULT_STYLE}'"


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


class Theme(dict):
    """One theme: its file's TOML table, plus where it came from and the
    catalog it was loaded into.

    A dict, so the charter hash and the validator read the file's own values.
    The properties and methods below are what the pipeline asks of a style;
    they read a charter as if it were complete, because build() refuses a
    theme its catalog's validate finds errors in before anything is billed.
    `valid`, `errors` and `warnings` are that verdict."""

    def __init__(self, key, table, source, path, catalog, parse_error=None):
        super().__init__(table)
        self.key = key
        self.source = source
        self.path = path
        self.preview = path.with_suffix(".jpg")
        self.parse_error = parse_error
        self.catalog = catalog

    @property
    def label(self):
        label = self.get("label")
        return label.strip() if isinstance(label, str) and label.strip() else self.key

    @property
    def errors(self):
        return self.catalog.validate(self.key)[0]

    @property
    def warnings(self):
        return self.catalog.validate(self.key)[1]

    @property
    def valid(self):
        return not self.errors

    # ---- the charter -----------------------------------------------------

    @property
    def charter(self):
        """The `[<style>.world]` charter as a block of prose, in
        _CHARTER_FIELDS order. The fields are fixed so that every style
        answers the same questions; validation says which are required."""
        world = self["world"]
        parts = [f"{label}: {str(world[key]).strip()}"
                 for key, label in _CHARTER_FIELDS
                 if world.get(key) and str(world[key]).strip()]
        return "\n".join(parts)

    @property
    def highlight(self):
        """What a highlight is in this style's world: the charter's
        `highlight`. The data key stays `anomaly`, so archived records and
        sweeps keep reading."""
        return self["world"]["highlight"].strip()

    @property
    def highlight_modes(self):
        """`highlight_modes` under `[<style>.world]`: the kinds of wrongness,
        one drawn per run."""
        return [str(v).strip() for v in self["world"]["highlight_modes"]]

    @property
    def has_myth(self):
        """Whether the charter has a `myth` field, which sends MYTH_LENS to the
        meaning call."""
        world = self.get("world") or {}
        return isinstance(world, dict) and bool(str(world.get("myth") or "").strip())

    @property
    def presence_allowed(self):
        """Whether the `presence` actor kind is eligible in this world.

        It shipped ineligible everywhere because most styles forbid the
        immaterial outright and the prohibitions it needed to read lived in
        `guidance`, which the charters replace. So it becomes what the actors
        brief proposed: a question each charter answers. A world that
        describes how the numinous takes a body can have one; a world that
        says nothing cannot, because silence is not permission.
        """
        world = self.get("world") or {}
        return bool(isinstance(world, dict) and str(world.get("numinous") or "").strip())

    @property
    def actor_kinds(self):
        """The kinds Stage 1.5 may choose from in this world."""
        kinds = self.catalog.actor_kinds
        if not self.presence_allowed:
            kinds = [k for k in kinds if k != "presence"]
        return kinds

    # ---- what Stage 2 is told --------------------------------------------

    @property
    def brief_rules(self):
        """The rules Stage 2's brief carries for this style: `[<style>.brief]`,
        in file order. Rule text may use the dial's string values as
        {placeholders} ({wonderTone}, {light}, ...)."""
        return [str(v).strip() for v in self["brief"].values()]

    def register_line(self, register):
        """What this register means in this style's world: the style's
        `[<style>.registers]` line. Validation requires one for every
        register, so there is no generic fallback."""
        if not register:
            return ""
        return str(self["registers"][register]).strip()

    def suffix_for(self, has_people=False):
        """The style's technique, appended to every image prompt.

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

        has_people defaults to False so a call that doesn't pass it gets
        suffix_base alone, never a surprise figure-rendering clause.
        """
        if "suffix_base" in self:
            suffix = self.get("suffix_base", "")
            if has_people and self.get("suffix_figures"):
                suffix += self["suffix_figures"]
            return suffix
        return self.get("suffix", "")

    # ---- what a run draws from ------------------------------------------

    @property
    def verbs(self):
        """`[<style>.verbs]` as {array: [verb, ...]}, the actor arrays as
        {activity: [verb, ...]} in ACTIVITIES order. Validation has already
        checked the counts and activities: a half-written list would quietly
        hand Stage 2 three verbs from a dozen."""
        table = self["verbs"]
        out = {}
        for name in VERB_ARRAYS:
            raw = table[name]
            if name.startswith("actor_"):
                out[name] = {k: [str(v).strip() for v in raw[k]] for k in ACTIVITIES if raw.get(k)}
            else:
                out[name] = [str(v).strip() for v in raw]
        return out

    @property
    def space(self):
        """`space` under `[<style>]`: the depth recipes, one drawn per run."""
        return [str(v).strip() for v in self["space"]]

    def light_for(self, exposure_band):
        """The style's `[<style>.light]` line for an exposure band."""
        return str(self["light"][exposure_band]).strip()


def final_image_prompt(image_prompt, style, has_people):
    """Stage 2's prompt joined with the style suffix: exactly what the image
    model is sent. build() and the sweep/replay harnesses all call this, so a
    sweep renders the prompt production would."""
    style_suffix = style.suffix_for(has_people)
    return f"{image_prompt}, {style_suffix}" if style_suffix else image_prompt


HIGHLIGHT_MODES_LEN = (4, 6)


def highlight_with_mode(highlight, dial):
    """The highlight definition with today's drawn kind of wrongness, if any."""
    mode = (dial or {}).get("highlightMode")
    return f"{highlight} Today's kind of wrongness: {mode}." if mode else highlight


class _BriefValues(dict):
    """format_map source for brief rules: the dial's values by key, with an
    unknown or missing key left visible as "{key}" rather than raising, so a
    typo in a theme file shows up in the prompt instead of crashing a run."""
    def __init__(self, dial):
        super().__init__({k: v for k, v in (dial or {}).items() if isinstance(v, str)})

    def __missing__(self, key):
        return "{" + key + "}"


# ---------------------------------------------------------------------------
# Rolling history (concept-level anti-repetition + register rotation)
# ---------------------------------------------------------------------------

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


# Objects and highlights are drawn fresh every run, but nothing stopped the
# same ones coming back: the archive has one day's divination cup and votive
# liver in 12 runs across 4 styles. The last few runs' material is passed to
# the material call as spent, the way recent constellations are to the meaning.
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


# Registers are grouped by what they're actually made of (registers.toml's
# families), used here to pick a *non-cohering* secondary. A pairing inside one
# group — botanical + bodily, architectural + domestic interior — collapses
# back into the single-material monoculture the pair exists to break, so the
# secondary is drawn from a different group whenever one is available.
def pick_registers(history, catalog, cohesion="collide"):
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
    registers = catalog.registers
    primary = pick_register(history, registers)
    if not primary:
        return None, None

    candidates = [r for r in registers if r != primary]
    if not candidates:
        return primary, None

    primary_family = catalog.register_family(primary)
    if cohesion == "cohere":
        # Same family, so the frame reads as one material world. Falling through
        # to None is a legitimate outcome, which the prompt already handles by
        # omitting the secondary line entirely (pre-1.5 history entries have a
        # null secondary). It used to happen to `mineral`, which held only
        # `geological` until the 2026-09-13 expansion gave it two partners; it
        # now happens only to the human registers, which are never eligible as a
        # secondary at all and so can never find a same-family one.
        near = [r for r in candidates if catalog.register_family(r) == primary_family]
        if not near:
            return primary, None
        pool = near
    else:
        distant = [r for r in candidates if catalog.register_family(r) != primary_family]
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


def stage1_cache_key(config):
    """What a cached reading must have been written under to be served: the
    birth data and the Stage 1 prompt's schema version (see PipelineState)."""
    return {"natalHash": _natal_hash(config), "schemaVersion": STAGE1_SCHEMA_VERSION}


def meaning_cache_key(config, style):
    """What a cached Stage 1.5 meaning must have been written under: the birth
    data, the style and its charter, and the Stage 1.5 schema version.

    styleKey joined as of 2026-09-20, because the style is an input: a ghibli
    amplification handed to a cyberpunk render would be exactly the
    foreign-object problem that change removed. Switching style mid-period
    re-bills Stage 1.5, which is correct and costs about $0.003. The charter
    is an input too: editing it must re-draw that day's meaning, not serve one
    written for the old wording."""
    return {
        "natalHash": _natal_hash(config),
        "styleKey": style.key,
        "charterHash": hashlib.sha256(style.charter.encode()).hexdigest()[:12],
        "schemaVersion": STAGE15_SCHEMA_VERSION,
    }



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


def stage1_interpret(reading, stage1_model, period_key, config, chat=None, state=None):
    """Cached per period_key, but keyed to the birth data too (see
    _natal_hash): regenerating the same day/week/month's image (e.g. while
    comparing style variants) shouldn't re-run Stage 1, but editing the
    birth date/time/location must invalidate the cache even within the same
    period, since Ascendant and transit-house placements feed straight into
    describe_natal()/describe_arc()'s prompt text below. Returns (result_dict,
    cost) — cost is None on a cache hit.

    `state` is the PipelineState whose readings cache is read and written;
    None (the default) neither reads nor writes one, for harnesses
    (sweep.py, build_style_gallery.py) that must leave production as it was."""
    cache_key = stage1_cache_key(config)
    cached = state.cached_reading(period_key, cache_key) if state else None
    if cached:
        return cached, None

    natal = reading["natal"]
    arc = reading["arc"]
    user_prompt = f"Natal: {describe_natal(natal)}.\n{describe_arc(arc)}"

    result, usage = (chat or openai_chat)(stage1_model, STAGE1_SYSTEM, user_prompt, "stage1", temperature=0.7, stage="stage1")
    for key in ("reading", "distillation", "narrativePosition"):
        if not result.get(key):
            raise PipelineError(f"Stage 1 response missing '{key}': {result!r}")

    result["generatedAt"] = datetime.now(timezone.utc).isoformat()
    result.update(cache_key)
    if state:
        state.store_reading(period_key, cache_key, result)
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
    # "a few paces from the viewer" (2026-10-10, itself a rewording) was copied
    # into 27% of 56 cross-style prompts the same day.
    "mid": "The event is in the middle ground, neither pressing on the viewer nor small at the back, with {deep} behind it, real but untroubled.",
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

# The exposure bands astro_engine sorts a day into. The light line for each
# band lives in the style's own `[<style>.light]` table since 2026-10-16
# (decisions 4 and 2): the global lines assumed an Earth sky, and every style
# now carries its own, so the pipeline keeps only the band names.
EXPOSURE_BANDS = ("lo", "mid", "hi")

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
# moved into the charters' `highlight`.
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
# "watching", reachable only from the pre-dial constants (deleted 2026-10-16).
#
# Two constraints from the melodrama audit are baked in here rather than left to
# the prompt: "fleeing" is out of the hard list, because it is the most
# reaction-shaped verb there is and would contradict the rule that no figure's
# job is to react; and "watching"/"listening to" are kept only in the sense of
# attending to a thing at work, never to a face.

# A reading with no texture is a broken reading, not a cell (2026-10-16).
# astro_engine.compute_texture always returns polarity, both bands and
# multiplicity, and astro-arc-generate always runs astro_engine, so a missing
# texture or band means something upstream failed. It used to degrade to
# NO_TEXTURE_DIAL, a fixed pre-dial cell that sent Stage 2 no light line at
# all; it is now an error. Harnesses that replay old days name a cell instead
# (replay_days.py and replay_amplify.py --cell).
TEXTURE_KEYS = ("polarity", "intensityBand", "exposureBand", "multiplicity")


def dial_from_texture(texture):
    """The day's imperatives. `texture` is arc["texture"] from astro_engine.
    A missing texture, or one missing any of TEXTURE_KEYS, is an error."""
    if not texture:
        raise PipelineError("The reading has no texture; astro_engine always computes one, "
                            "so the reading is broken. Regenerate it.")
    missing = [k for k in TEXTURE_KEYS if texture.get(k) is None]
    if missing:
        raise PipelineError(f"The reading's texture is missing {', '.join(missing)}; "
                            "astro_engine always supplies them, so the reading is broken.")

    polarity = texture["polarity"]
    # A day with no aspect in orb (1 in 365) is a defined cell, not a fallback:
    # astro_engine says "none", and it reads as soft at minimum intensity. An
    # exception branch is how a neutral day ends up looking like a crash.
    if polarity == "none":
        polarity = "soft"
    intensity_band = texture["intensityBand"]
    exposure_band = texture["exposureBand"]
    multiplicity = texture["multiplicity"]

    for max_hits, sites, objects, things, words in _DENSITY_BY_MULTIPLICITY:
        if multiplicity <= max_hits:
            break

    return {
        "mode": "coherent",
        # Kept so the per-style draws (draw_style_choices) know which half of
        # each array to draw from without re-reading the texture.
        "polarity": polarity,
        "intensityBand": intensity_band,
        # Picks the line from the style's [light] table (draw_style_choices).
        "exposureBand": exposure_band,
        "sites": sites,
        "objectRange": objects,
        "thingRange": things,
        "wordRange": words,
        # verbs, actorVerbs, light and the rest come from the style's own
        # charter, in draw_style_choices.
        "registerCohesion": "collide" if polarity == "hard" else "cohere",
        "spatial": _SPATIAL_BY_INTENSITY[intensity_band],
        "prominence": _PROMINENCE_BY_INTENSITY[intensity_band],
        "wonderTone": _WONDER_BY_POLARITY["hard" if polarity == "hard" else "soft"],
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


# How worn things are, drawn per run for every style (STYLE-OVERHAUL Prompt 1,
# Step 5). Wear used to be a constant -- the highlight, THE IMPOSSIBLE, the
# soft/lo actor relation and both charters all required it -- so every image
# came back weathered. Hard days lean to the far end, soft days to the near
# one; nothing is ruled out on either. The scale is the pipeline's, the same
# for every style (the per-style `conditions` override went 2026-10-16,
# unused).
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


# Invariant, the same for every style with a [space] array: it keeps what
# OPENNESS and DEPTH were for (a wallpaper must not be claustrophobic) without
# fixing where the depth is.
WALLPAPER_RULE = ("WALLPAPER — The frame always has somewhere for the eye to go past the subject; "
                  "today's space line says where. A sealed close-up with nothing past the subject "
                  "is a failed response. Name what is there rather than that it is far: not "
                  "\"beyond\", \"in the distance\" or \"far off\".")


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


def draw_style_choices(style, dial, history, period_key):
    """This run's draws from the style's charter arrays, and the dial with them
    applied: (dial, draws). `draws` is what goes into the history entry (so the
    next run can exclude it) and into pipeline-meta (so the run page can show
    it). Keys a style has no array for are simply absent."""
    style_key = style.key
    draws = {}
    rng = _style_rng(period_key, style_key, history)
    hard = dial["polarity"] == "hard"

    verbs = style.verbs
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

    space = style.space
    if space:
        draws["space"] = _draw(rng, space, _recent_draws(history, style_key, "space"), 1)[0]

    used = _recent_draws(history, style_key, "condition")
    pool = [c for c in CONDITIONS if c not in used] or list(CONDITIONS)
    weights = _CONDITION_WEIGHTS["hard" if hard else "soft"]
    draws["condition"] = rng.choices(pool, weights=[weights.get(c, 1) for c in pool])[0]

    modes = style.highlight_modes
    if modes:
        draws["highlightMode"] = _draw(rng, modes, _recent_draws(history, style_key, "highlightMode"), 1)[0]

    return apply_draws(style, dial, draws), draws


def apply_draws(style, dial, draws):
    """The dial with a set of draws applied, whether drawn just now or pinned
    from an earlier run (see compose). The light line is not a draw: the
    style's line for today's exposure band always applies."""
    dial = dict(dial)
    dial["verbs"] = ", ".join(draws["processVerbs"])
    dial["actorVerbs"] = ", ".join(draws["actorVerbs"])
    dial["activity"] = draws["activity"]
    if "space" in draws:
        dial["space"] = draws["space"]
    dial["light"] = style.light_for(dial["exposureBand"])
    dial["condition"] = draws["condition"]
    if "highlightMode" in draws:
        dial["highlightMode"] = draws["highlightMode"]
    return dial


# ---- The charter validator (charter-required, 2026-10-16) ------------------
#
# The one place that says what a complete charter is (decision 2 in
# docs/decisions/2026-10-styles.md). There are no neutral fallbacks any more:
# Theme's accessors read a charter as if it were complete, and build() refuses
# a style this finds errors in before anything is billed. Each error names the
# style, the field and the rule, so whoever wrote the charter can fix it from
# the message alone. Warnings are choices the schema allows but advises
# against (decision 8); they are printed and never block a run.

# The [world] fields a charter must have: everything Stage 2 reads out except
# `forbids` (optional; no shipped style uses it), plus the highlight.
CHARTER_WORLD_FIELDS = tuple(k for k, _ in _CHARTER_FIELDS if k != "forbids") + (
    "highlight", "highlight_modes")


def _text(value):
    return str(value).strip() if isinstance(value, str) else ""


def _flat_verbs(value):
    """A verb array as one list, the actor tables' activities concatenated."""
    if isinstance(value, dict):
        return [v for group in value.values() if isinstance(group, list) for v in group]
    return list(value) if isinstance(value, list) else []


def _brief_keys(style):
    """The dial keys a brief rule may use as {placeholders}: the string values
    of a real dial after this style's draws, which is exactly what
    _BriefValues hands format_map."""
    texture = {"polarity": "soft", "intensityBand": "mid", "exposureBand": "mid", "multiplicity": 0}
    dial = dial_from_texture(texture)
    try:
        dial, _ = draw_style_choices(style, dial, [], "validate")
    except Exception:  # an incomplete charter; its errors are reported elsewhere
        pass
    return {k for k, v in dial.items() if isinstance(v, str)}


# Studio and franchise names a theme's model-facing text must not use (the
# skill's rule; decision 10 is the open question for the shipped ghibli
# suffix). Historical artists and periods are fine and not listed. A living
# artist cannot be listed at all, so the skill carries that rule alone.
PROTECTED_NAMES = ("ghibli", "miyazaki", "pixar", "disney", "dreamworks", "marvel",
                   "star wars", "pokemon", "pokémon", "nintendo", "warhammer",
                   "studio trigger", "makoto shinkai")


def _theme_strings(value, path=""):
    """Every string in a theme table, with its field path."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _theme_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        for v in value:
            yield from _theme_strings(v, path)


def _validate_charter(catalog, style_key):
    """(errors, warnings) for one theme of `catalog` (see Catalog.validate).
    An empty error list means the charter is complete, the preview exists,
    and the theme can be used for a run. The verdict reads the rest of the
    catalog too: the registers it must have a line for, and the other themes
    its verbs should not share."""
    styles = catalog.styles
    errors, warnings = [], []

    def error(field, rule):
        errors.append(f"{style_key}: {field}: {rule}")

    def warn(field, note):
        warnings.append(f"{style_key}: {field}: {note}")

    style = styles.get(style_key)
    if not isinstance(style, dict):
        return [f"{style_key}: no such theme"], []
    if getattr(style, "parse_error", None):
        return [f"{style_key}: {style.path.name} is not valid TOML: {style.parse_error}"], []
    preview = getattr(style, "preview", None)
    if preview is not None and not preview.is_file():
        error("preview", f"{preview.name} is required beside {style.path.name}")

    if not _text(style.get("label")):
        error("label", "required")
    if not (_text(style.get("suffix")) or _text(style.get("suffix_base"))):
        error("suffix", "a `suffix` or a `suffix_base` is required")

    def table(name):
        value = style.get(name)
        if not isinstance(value, dict):
            error(f"[{name}]", "table required")
            return {}
        return value

    world = table("world")
    for key in CHARTER_WORLD_FIELDS:
        if key == "highlight_modes":
            continue
        if not _text(world.get(key)):
            error(f"[world] {key}", "required")
    if world:
        modes = world.get("highlight_modes")
        lo, hi = HIGHLIGHT_MODES_LEN
        if not isinstance(modes, list):
            error("[world] highlight_modes", f"required, an array of {lo} to {hi} kinds of wrongness")
        else:
            modes = [_text(m) for m in modes]
            if not all(modes):
                error("[world] highlight_modes", "every entry must be non-empty text")
            elif not lo <= len(modes) <= hi:
                error("[world] highlight_modes", f"has {len(modes)} entries; it needs {lo} to {hi}")

    brief = table("brief")
    if brief:
        for key in ("signature", "temper"):
            if not _text(brief.get(key)):
                error(f"[brief] {key}", "required")
        allowed = _brief_keys(style)
        moods = _CAPTION_EMOTION | _CAPTION_MOTIVE | _CAPTION_SIDES
        for key, rule in brief.items():
            rule = _text(rule)
            if not rule:
                error(f"[brief] {key}", "must be non-empty text")
                continue
            fields = {f for _, f, _, _ in string.Formatter().parse(rule) if f}
            for name in sorted(fields - allowed):
                error(f"[brief] {key}", f"{{{name}}} is not a value the dial supplies "
                                        f"({', '.join(sorted(allowed))})")
            # TEMPER carries the dial's temper ({wonderTone}); it is not a
            # rule with a failure condition and is never written as one.
            if key != "temper" and "failed response" not in rule.lower():
                warn(f"[brief] {key}", 'has no "failed response" condition')
            words = set(re.findall(r"[a-z]+", re.sub(r"\{[^}]*\}", " ", rule.lower())))
            if words & moods:
                warn(f"[brief] {key}", "requires a mood or relationship "
                                       f"({', '.join(sorted(words & moods))}); allowed, "
                                       "but it is the creator's choice, not the chart's")

    verbs = table("verbs")
    if verbs:
        lo, hi = VERB_ARRAY_LEN
        for name in VERB_ARRAYS:
            raw = verbs.get(name)
            field = f"[verbs] {name}"
            if name.startswith("actor_"):
                if not isinstance(raw, dict):
                    error(field, f"required, a table keyed by activity ({', '.join(ACTIVITIES)})")
                    continue
                unknown = sorted(set(raw) - set(ACTIVITIES))
                if unknown:
                    error(field, f"unknown activity {', '.join(unknown)}; use {', '.join(ACTIVITIES)}")
                present = {k for k in ACTIVITIES if isinstance(raw.get(k), list) and raw.get(k)}
                missing = [k for k in ("work", "rest", "travel") if k not in present]
                if missing or not ({"play", "ritual"} & present):
                    error(field, "needs work, rest and travel, plus play or ritual"
                                 + (f" (missing {', '.join(missing)})" if missing else ""))
            elif not isinstance(raw, list):
                error(field, f"required, an array of {lo} to {hi} verbs")
                continue
            values = [_text(v) for v in _flat_verbs(raw)]
            if not all(values):
                error(field, "every entry must be non-empty text")
            if not lo <= len(values) <= hi:
                error(field, f"has {len(values)} entries; it needs {lo} to {hi}")
            repeats = sorted({v for v in values if v and values.count(v) > 1})
            if repeats:
                error(field, f"repeats {', '.join(repeats)}")
            for other, other_style in styles.items():
                if other == style_key or not isinstance(other_style, dict):
                    continue
                theirs = other_style.get("verbs")
                if not isinstance(theirs, dict):
                    continue
                shared = sorted(set(values) & {_text(v) for v in _flat_verbs(theirs.get(name))} - {""})
                if shared:
                    warn(field, f"shares {', '.join(shared)} with {other}'s {name}")

    registers = style.get("registers")
    if not isinstance(registers, dict):
        error("[registers]", "table required")
    else:
        for register in catalog.registers:
            if not _text(registers.get(register)):
                error(f"[registers] {register}", "required (one line per register in registers.toml)")

    space = style.get("space")
    lo, hi = SPACE_ARRAY_LEN
    if not isinstance(space, list):
        error("space", f"required, an array of {lo} to {hi} depth recipes")
    elif not all(_text(v) for v in space):
        error("space", "every recipe must be non-empty text")
    elif not lo <= len(space) <= hi:
        error("space", f"has {len(space)} recipes; it needs {lo} to {hi}")

    light = table("light")
    if light:
        if set(light) != set(EXPOSURE_BANDS):
            error("[light]", f"needs exactly {', '.join(EXPOSURE_BANDS)} "
                                         f"(has {', '.join(sorted(light)) or 'nothing'})")
        for band in EXPOSURE_BANDS:
            if band in light and not _text(light[band]):
                error(f"[light] {band}", "must be non-empty text")

    named = {}
    for field, text in _theme_strings({k: v for k, v in style.items() if k != "label"}):
        low = text.lower()
        for name in PROTECTED_NAMES:
            if re.search(rf"\b{re.escape(name)}\b", low):
                named.setdefault(field.split(".")[0], set()).add(name)
    for field, names in sorted(named.items()):
        warn(field, f"names a protected studio or franchise ({', '.join(sorted(names))}); "
                    "describe what the frame looks like instead")

    return errors, warnings


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
# archetype" rule plus the RECENTLY USED list (see
# PipelineState.recent_constellations). Every amplification cached under the
# old wording was one of the two archetypes this change exists to break out
# of, so none of them should survive it.
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


def stage15_meaning(stage1_result, model, period_key, config, state=None, dial=None,
                    avoid_archetypes=None, avoid_kinds=None, style=None, chat=None):
    """The MEANING half of Stage 1.5: constellation, movement, affect, actor and
    actorKind. Split from the material half on 2026-09-26 (see stage15_amplify)
    so that the day's meaning is fixed while its material is not.

    Cached per period_key exactly like stage1_interpret() — regenerating
    the same day's image must not re-bill this call — and invalidated by
    meaning_cache_key's inputs: the birth data, the style, its charter and a
    schema version.

    Uses Stage 1's model and key slot, not Stage 2's: this is meaning-work,
    not imagery-work.

    `state` is the PipelineState whose meanings cache is read and written;
    None (the default) is for harnesses like sweep.py, which need a fresh
    amplification per run and must never write into the production cache.

    `dial` (see dial_from_texture) sets how many objects to ask for and which
    tonal direction to push, and is STORED IN THE CACHE ENTRY. That storage
    matters: regenerating the same day re-uses this object list but would
    recompute the dial from a later Moon position, so a cached list could end up
    paired with a density it was never written for. Same-day re-runs are normal
    here — history.json holds three 2026-09-12 entries sharing one object set.

    Returns (result_dict, cost) — cost is None on a cache hit.
    """
    if dial is None:
        raise PipelineError("Stage 1.5 needs the day's dial (dial_from_texture)")
    if style is None:
        raise PipelineError("Stage 1.5 needs the style (a Theme from the run's catalog)")
    cache_key = meaning_cache_key(config, style)
    cached = state.cached_meaning(period_key, cache_key) if state else None
    if cached:
        return cached, None

    kinds = style.actor_kinds
    catalog = style.catalog
    system_prompt = (
        STAGE15_SYSTEM
        # Injected rather than written into the prompt, so the enum cannot fall
        # out of step with actors.toml — the same reason the people-register
        # list used to be injected into STAGE2_SYSTEM.
        .replace("{{ACTOR_KINDS}}", ", ".join(kinds) or "none")
        .replace("{{ACTOR_KIND_TABLE}}", "\n".join(
            f"    {k} — {catalog.actor_kind_summary(k)}" if catalog.actor_kind_summary(k)
            else f"    {k} — no one at all; the materials and the place carry the image alone"
            for k in kinds) or "    none")
    )

    user_lines = []
    style_world = style.charter
    if style_world:
        user_lines.append(
            "THE WORLD — every object you name, and the anomaly, must belong to it:\n"
            + style_world
        )
        if style.has_myth:
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

    # Recency pressure on the archetype, for the reason AMPLIFY_AVOID_WINDOW
    # documents. Passed as data in the user message rather than baked into the
    # system prompt, exactly like Stage 2's avoided concepts: the rule is policy
    # and belongs above, the list changes every run and belongs here. The
    # caller passes the lists: build() hands in the real ones from its state
    # (PipelineState.recent_constellations, recent_actor_kinds), a harness its
    # own. None is no recency, never "go and read the production cache".
    avoid_archetypes = avoid_archetypes or []
    avoid_kinds = avoid_kinds or []
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

    result, usage = (chat or openai_chat)(model, system_prompt, "\n".join(user_lines), "stage1", temperature=0.95, stage="stage15")

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
    result.update(cache_key)
    if state:
        state.store_meaning(period_key, cache_key, result)
    return result, chat_call_cost(model, usage)


def stage15_material(stage1_result, meaning, model, config, dial=None, style=None,
                     register=None, secondary_register=None, avoid_material=None, chat=None):
    """The MATERIAL half of Stage 1.5: objects and the highlight (key
    `anomaly`, see Theme.highlight), built
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
        raise PipelineError("Stage 1.5 needs the day's dial (dial_from_texture)")
    object_lo, object_hi = dial["objectRange"]
    system_prompt = (
        STAGE15_MATERIAL_SYSTEM
        .replace("{{OBJECT_RANGE}}", f"{object_lo} to {object_hi}")
    )

    if style is None:
        raise PipelineError("Stage 1.5 needs the style (a Theme from the run's catalog)")
    user_lines = []
    style_world = style.charter
    if style_world:
        user_lines.append(
            "THE WORLD — every piece you name, and the highlight, must belong to it:\n"
            + style_world
        )
    if register:
        line = style.register_line(register)
        user_lines.append(f"PRIMARY REGISTER — at least half the pieces, and the highlight, are made of this: {register}"
                          + (f"\n  In this world: {line}" if line else ""))
    if secondary_register:
        line = style.register_line(secondary_register)
        user_lines.append(f"SECONDARY REGISTER — may supply one or two pieces: {secondary_register}"
                          + (f"\n  In this world: {line}" if line else ""))
    user_lines.append("THE HIGHLIGHT — what it is in this world: "
                      + highlight_with_mode(style.highlight, dial))
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

    result, usage = (chat or openai_chat)(model, system_prompt, "\n".join(user_lines), "stage1",
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


def stage15_amplify(stage1_result, model, period_key, config, state=None, dial=None,
                    avoid_archetypes=None, avoid_kinds=None, style=None,
                    register=None, secondary_register=None, material=True,
                    avoid_material=None, chat=None):
    """Stage 1.5, both halves: the day's MEANING (cached per period, see
    stage15_meaning) and fresh MATERIAL for this register (never cached, see
    stage15_material), merged into the one dict Stage 2 and the records have
    always taken. Pick the registers BEFORE calling this -- they are an input.

    `material=False` returns the meaning alone, for harnesses that only
    measure the archetype and actor (replay_amplify.py).

    Returns (result_dict, cost); cost is None only when the meaning was a cache
    hit and no material was asked for."""
    meaning, meaning_cost = stage15_meaning(
        stage1_result, model, period_key, config, state=state, dial=dial,
        avoid_archetypes=avoid_archetypes, avoid_kinds=avoid_kinds,
        style=style, chat=chat)
    if not material:
        return meaning, meaning_cost
    mat, material_cost = stage15_material(
        stage1_result, meaning, model, config, dial=dial, style=style,
        register=register, secondary_register=secondary_register,
        avoid_material=avoid_material, chat=chat)
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


def caption_model_for(provider):
    """CAPTION_MODEL under `provider`'s naming, so the judge is the same model
    the calibration was scored on either way."""
    return "openai/" + CAPTION_MODEL if provider == "openrouter" else CAPTION_MODEL


def caption_check(prompt, model, chat=None):
    """(caption, hits, passed) — would this image be filed under a feeling?

    `hits` lists the matched words with their category, so a failure can be
    read rather than guessed at, and so the lists can be tuned against a
    labelled calibration set instead of by intuition.

    Never raises. A judge that errors or times out returns passed=None, which
    the caller treats as "not checked" rather than "failed": losing a paid
    render because the cheap check fell over would be a bad trade.
    """
    try:
        result, _ = (chat or openai_chat)(model, CAPTION_CHECK_SYSTEM, prompt, "stage2",
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


def stage2_image_prompt(stage1_result, register, avoid_tags, stage2_model, style,
                         secondary_register=None, amplification=None, dial=None, chat=None):
    """`style` is the run's Theme: its charter, register lines, brief and
    highlight, and through its catalog the cliches and actor kinds.
    `amplification` is Stage 1.5's output (see stage15_amplify). It defaults
    to None so the function still works without a Stage 1.5 result — sweep.py
    relies on that to produce a pre-Stage-1.5 baseline against the same code."""
    catalog = style.catalog
    style_world = style.charter
    register_guidance = style.register_line(register)
    secondary_register_guidance = style.register_line(secondary_register)
    style_brief = style.brief_rules
    highlight = style.highlight
    cliches = catalog.cliches
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


    # The world charter only. The style's `label` is display-only and never
    # reaches a model: a prompts-only comparison (decision 3,
    # docs/decisions/2026-10-styles.md) found no loss without it.
    if style_world:
        user_lines.append(f"Rendering style for this image: {style_world}")
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
    if dial is None or style_brief is None or not highlight:
        raise PipelineError("stage2_image_prompt needs the dial, the style's brief and its "
                            "highlight; build() passes all three")
    # Who is in the scene, from Stage 1.5. An amplification written before the
    # actor existed has neither field, and rather than defaulting to "none" —
    # which is the behaviour this change exists to stop being the default — an
    # actorless amplification falls back to `solitary`, keeping a body in the
    # frame. Excluded pairings (automaton in a mechanical world) are vetoed
    # here, where the register is known and Stage 1.5's is not.
    actor = (amplification or {}).get("actor") or ""
    actor_kind = (amplification or {}).get("actorKind") or ""
    if amplification and not actor_kind:
        actor_kind = "solitary" if "solitary" in catalog.actor_kinds else "none"
    if actor_kind in catalog.actor_excluded_for(register):
        actor_kind = "solitary" if "solitary" in catalog.actor_kinds else "none"
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
        f"  What a highlight is in this world: {highlight_with_mode(highlight, dial)}",
        f"  How prominent the highlight is in this image: {dial['prominence']}",
        f"  The highlight, and how the actor treats it: {dial['anomalyRelation']}",
    ]
    # The "THE ANOMALY IS ENORMOUS, put PART of it in shot" rule that sat here
    # (fired only for scale anomalies) moved into the charters' `highlight` on
    # 2026-10-09, with the scale/time typing it keyed on.
    # The actor goes in the brief rather than STAGE2_SYSTEM for the reason the
    # register-contribution and openness rules did: the brief is demonstrably
    # the part that gets obeyed. Three relations, not one — the anomaly line
    # above has always existed, and the objects and the site are the two that
    # were missing, which is where "objects strewn about" actually lived.
    if actor_kind and actor_kind != "none":
        who = f"{actor} ({actor_kind})" if actor else actor_kind
        brief += [
            f"  THE ACTOR — required, and not an extra: {who}.",
            f"    How this kind is framed: {catalog.actor_guidance(actor_kind) or 'no further framing.'}",
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
    # The style's own rules, from `[<style>.brief]` (Theme.brief_rules), each
    # phrased as a count and a failure condition because mood words are not
    # obeyed. They sit in the brief rather than STAGE2_SYSTEM because the brief
    # is demonstrably the part that gets obeyed. Until 2026-09-26 THE
    # IMPOSSIBLE and OPENNESS were hardcoded here for every style; both were
    # written for ghibli's world. Every style has had its own since
    # 2026-10-10, and since 2026-10-16 a charter without them cannot run.
    for rule in style_brief:
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

    result, usage = (chat or openai_chat)(stage2_model, stage2_system(), "\n".join(user_lines), "stage2", temperature=0.95, stage="stage2")
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

CAPTION_POLICIES = ("resample", "record", "skip")
# Keys compose() returns beyond build()'s metadata. build() drops them, so its
# metadata (and every pipeline-meta-*.json) keeps exactly the shape it had.
_COMPOSE_ONLY_KEYS = ("meaning", "material", "meaningCost", "materialCost")


def compose(stage1, arc, dial, style, config, history, *, chat=None,
            caption="resample", draws=None, registers=None, meaning=None,
            avoided_concepts=None, avoid_archetypes=(), avoid_kinds=(),
            state=None):
    """One composition: a Stage 1 result to a final image prompt. Production
    (build) and every harness run this, so a harness measures the code that
    ships rather than a copy of its sequence.

    Inputs:
      stage1      Stage 1's result (reading, distillation, narrativePosition).
      arc         the reading's arc: periodKey, frequency, and texture (the
                  texture is only recorded; the dial is what acts).
      dial        dial_from_texture's dial, or a harness's own.
      style       the Theme, from the run's catalog, whose charter has passed
                  catalog.require. Its catalog supplies the registers, actor
                  kinds and cliches.
      history     the runs this one's recency comes from, newest first. Never
                  read from or written to disk here: production passes
                  state.history(), a harness passes its own list and inserts
                  the returned historyEntry to move recency on.

    Keywords:
      chat        the chat client (see openai_chat); None means OpenAI.
      caption     the melodrama check: "resample" (production: a failure
                  re-runs Stage 2 up to CAPTION_RESAMPLES times), "record"
                  (check once, never resample) or "skip" (no check).
      draws, registers, meaning
                  pins: supplied, they replace what this run would choose.
                  `draws` is an earlier run's draws, `registers` a
                  (primary, secondary) pair, `meaning` a Stage 1.5 meaning
                  (only the material half then runs).
      avoided_concepts  overrides the concepts worked out from `history`.
      avoid_archetypes, avoid_kinds
                  Stage 1.5's recent constellations and actor kinds. Empty by
                  default; production passes the real ones.
      state       the PipelineState whose Stage 1.5 meanings cache is read and
                  written. None (the default) uses no cache; build() passes
                  its own. History and recency never come from it here.

    Returns build()'s metadata plus the meaning and material halves of Stage
    1.5 and their costs. The history entry is in it under "historyEntry"; it
    is never saved here (see build)."""
    if caption not in CAPTION_POLICIES:
        raise PipelineError(f"compose: caption must be one of {CAPTION_POLICIES}, not {caption!r}")
    stage1_model = config.get("stage1Model") or "gpt-4o-mini"
    stage2_model = config.get("stage2Model") or "gpt-4o-mini"
    period_key = arc["periodKey"]
    style_key = style.key
    catalog = style.catalog

    # Per-style draws (verbs, ...) from the charter's arrays, never repeating
    # what this style drew in its last STYLE_RECENCY runs. Before Stage 1.5,
    # because the dial they change is one of its inputs.
    if draws is None:
        dial, draws = draw_style_choices(style, dial, history, period_key)
    else:
        dial = apply_draws(style, dial, draws)
    # Registers are picked BEFORE Stage 1.5 as of 2026-09-26, because they are
    # now one of its inputs: the material half builds the objects and the
    # anomaly out of the primary register. Picked after, the register could
    # only reach Stage 2 as an adjective on a scene already cast without it.
    if registers is None:
        register, secondary_register = pick_registers(history, catalog, dial["registerCohesion"])
    else:
        register, secondary_register = registers
    meaning_cost = None
    if meaning is None:
        meaning, meaning_cost = stage15_meaning(
            stage1, stage1_model, period_key, config, state=state, dial=dial,
            avoid_archetypes=list(avoid_archetypes), avoid_kinds=list(avoid_kinds),
            style=style, chat=chat)
    material, material_cost = stage15_material(
        stage1, meaning, stage1_model, config, dial=dial, style=style,
        register=register, secondary_register=secondary_register,
        avoid_material=recent_material(history), chat=chat)
    amplification = {**meaning, **material}
    stage15_cost = (meaning_cost or 0.0) + (material_cost or 0.0)
    avoid = avoid_concepts(history) if avoided_concepts is None else list(avoided_concepts)

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
    for _attempt in range(1 + (CAPTION_RESAMPLES if caption == "resample" else 0)):
        image_prompt, concept_tags, has_people, attempt_cost = stage2_image_prompt(
            stage1, register, avoid, stage2_model, style,
            secondary_register=secondary_register,
            amplification=amplification,
            dial=dial,
            chat=chat,
        )
        stage2_cost += attempt_cost or 0.0
        if caption == "skip":
            break
        caption_text, hits, passed = caption_check(image_prompt, caption_model, chat=chat)
        caption_attempts.append({"caption": caption_text, "hits": hits, "passed": passed})
        if passed is not False:
            break

    # has_people is already known by this point (stage2_image_prompt above
    # returned it) — no call-ordering change needed, just threading the
    # value through so a suffix_base/suffix_figures preset (see
    # Theme.suffix_for) only pays for figure-rendering instructions on a
    # generation that actually has a figure in it.
    final_prompt = final_image_prompt(image_prompt, style, has_people)

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "periodKey": period_key,
        "frequency": arc["frequency"],
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

    return {
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
        # caught something and Stage 2 was resampled. None when caption="skip".
        "captionCheck": ({"model": caption_model, "attempts": caption_attempts,
                          "passed": caption_attempts[-1]["passed"]}
                         if caption_attempts else None),
        # Which model actually ran each stage. Previously only the COSTS were
        # recorded, so a review page could show what a stage cost but not what
        # produced it — and with the models now freely switchable from the
        # widget, a cost with no model attached is close to meaningless.
        "stage1Model": stage1_model,
        "stage15Model": stage1_model,  # Stage 1.5 deliberately runs on Stage 1's model
        "stage2Model": stage2_model,
        "pipelineMode": dial["mode"],
        "texture": arc.get("texture"),
        "dial": {k: v for k, v in dial.items() if k != "mode"},
        "draws": draws,
        "avoidedConcepts": avoid,
        "artStyle": style_key,
        # The display name for that key (the theme file's own `label`), so
        # anything captioning a run — the "Themes Generated" entry's
        # title, the exported theme's README — can name the style the
        # way the Style dropdown does without re-reading the TOML.
        "artStyleLabel": style["label"],
        "imagePrompt": image_prompt,
        "finalPrompt": final_prompt,
        # stage1Cost is set by build(), which runs Stage 1; a harness that
        # brought its own Stage 1 result has no such cost.
        "stage1Cost": None,
        "stage15Cost": stage15_cost,
        "stage2Cost": stage2_cost,
        "meaning": meaning,
        "material": material,
        "meaningCost": meaning_cost,
        "materialCost": material_cost,
    }


def build(reading, config, chat=None, state=None, catalog=None):
    """Production's composition: Stage 1, the real history and the real
    Stage 1.5 recency and cache, the melodrama resample. Returns
    (final_prompt, metadata), the metadata astro-arc-generate writes.

    `state` is where the history and caches come from: production's
    PipelineState() unless a test or the audit passes one rooted elsewhere.
    build() only reads the history (see commit_history). `catalog` is the
    themes, registers and actors it chooses from: loaded fresh unless a test
    or the audit passes its own."""
    state = state or PipelineState()
    catalog = catalog or Catalog.load()
    stage1_model = config.get("stage1Model") or "gpt-4o-mini"
    style_key = catalog.resolve(config.get("artStyle"))
    catalog.require(style_key)
    style = catalog.style(style_key)
    period_key = reading["arc"]["periodKey"]

    # The dial has to exist BEFORE Stage 1.5, because it sets how many objects to
    # ask for and gets stored in that stage's cache entry.
    dial = dial_from_texture(reading["arc"].get("texture"))

    stage1, stage1_cost = stage1_interpret(reading, stage1_model, period_key, config,
                                           chat=chat, state=state)
    result = compose(
        stage1, reading["arc"], dial, style, config, state.history(), chat=chat,
        caption="resample",
        # Production's Stage 1.5 recency comes from the amplification cache,
        # this period excluded (see PipelineState.recent_constellations).
        avoid_archetypes=state.recent_constellations(AMPLIFY_AVOID_WINDOW, exclude=period_key),
        avoid_kinds=state.recent_actor_kinds(ACTOR_VETO_WINDOW, exclude=period_key),
        state=state,
    )
    # NOT saved here as of 2026-10-08. build() runs before the image call, so
    # saving the entry now meant a render refused by the provider still counted
    # as a generation: the register rotation and the avoided-concepts window
    # both moved on for a day that produced no image. The entry rides in the
    # metadata instead, and astro-arc-generate commits it with
    # `llm_pipeline.py --commit-history` once the image exists.
    for key in _COMPOSE_ONLY_KEYS:
        result.pop(key)
    # Cost of the chat calls this run actually made — None means
    # "cached, no call made" (stage1/stage1.5) or "model not
    # in cost_estimate.py's rate table" (unknown, not zero).
    result["stage1Cost"] = stage1_cost
    return result["finalPrompt"], result


def next_run_cached(config, period_key, state=None, catalog=None):
    """(Stage 1 cached, Stage 1.5's meaning cached) for a run of `config` in
    `period_key`: the question the cost estimate asks before a run, answered
    with the keys build() would look up. `state` and `catalog` default to
    production's."""
    state = state or PipelineState()
    catalog = catalog or Catalog.load()
    meaning_key = meaning_cache_key(config, catalog.style(catalog.resolve(config.get("artStyle"))))
    return (state.cached_reading(period_key, stage1_cache_key(config)) is not None,
            state.cached_meaning(period_key, meaning_key) is not None)


def commit_history(metadata_path, state=None):
    """Append the history entry build() left in a run's metadata. Called by
    astro-arc-generate only after the image has rendered, so a refused or
    failed render never advances the rotation. A metadata file with no entry
    (written before 2026-10-08) is a no-op rather than an error."""
    metadata = json.loads(Path(metadata_path).read_text())
    entry = metadata.get("historyEntry")
    if entry:
        (state or PipelineState()).append_history(entry)


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

    config = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}

    try:
        final_prompt, metadata = build(reading, config)
    except (ApiKeyError, PipelineError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    Path(sys.argv[2]).write_text(json.dumps(metadata, indent=2))
    print(final_prompt)


if __name__ == "__main__":
    main()
