# How one image gets made

One generation turns a birth chart and today's sky into a wallpaper and a
matching desktop palette. At the Ultra preset (`gpt-5.4` for writing,
`gpt-image-2.5-sunburst` at `high`) a fresh day costs about $0.077 and a
same-day re-run about $0.066 (measured 2026-10-10: a run that re-drew the day's
meaning charged $0.0724): the reading and the day's meaning are cached, the
material, the prompt and the picture are remade. The render is most of the cost
and most of the time. The panel's cost line projects the real figure for
whatever preset is set.

**At a glance:** $0.077 fresh run · $0.066 same-day re-run (Ultra) · ~40s render · 20 registers · 7 worlds, plus your own

Thirteen steps. Five of them call a language model: the reading, the meaning,
the material, the prompt and the melodrama check. This document walks the
whole path in order, and every step names the file that runs it, what it reads,
what it writes, whether it is cached, and **the one knob that changes it** — so
you can retune this thing without reading any Python.

If you only read one section, read [Tweak it without touching
code](#tweak-it-without-touching-code).

> This file is the source. `docs/pipeline.html` is generated from it by
> `backend/bin/build_pipeline_page.py` — edit here, run that, never edit the
> HTML. `build_pipeline_page.py --check` fails if the two have drifted.

---

<!-- page:skip -->
## The map

```
  SCHEDULER  the panel's 5-minute poll ───┐
                                          ▼
  0  DECIDE      astro-arc-generate --if-due      is this period already done?
                                          │
  1  CHART       astro_engine.py                  ephemeris → planets, houses, texture
                                          │
  ╭───────────────── llm_pipeline.py ──────────────────────────────────╮
  │ 2  READING     Stage 1      what the sky means for this person     │  cached / day
  │ 3  DIAL        (no LLM)     density, light, how the actor behaves  │  free
  │ 4  REGISTERS   (no LLM)     what the scene is MADE of              │  free
  │ 5  MEANING     Stage 1.5    the myth, and who is in it             │  cached / day
  │ 6  MATERIAL    Stage 1.5    objects + highlight, from the register │  every run
  │ 7  PROMPT      Stage 2      the actual image prompt                │  every run
  │ 8  CHECK       caption      would it be filed under a feeling?     │  every run
  ╰────────────────────────────────────────────────────────────────────╯
                                          │
  9  RENDER      openai_image_gen.py               prompt + style suffix → PNG
                                          │
 10  PALETTE     palette_extract.py                PNG → colors.toml
                                          │
 11  RECORD      build_review.py                   history + archive + gallery + cost log
                                          │
 12  APPLY       omarchy-theme-bg-set              wallpaper goes live (+ palette if opted in)
```

Steps 2–8 are the interesting part and all live in one file. Steps 0, 1 and
9–12 are plumbing.

---

## The pipeline

Steps 2–8 all live in one file, `llm_pipeline.py`, and are where the picture is
actually decided. Everything else is plumbing. The steps that spend money are
marked.

## 0. Decide whether to run at all

**Runs:** `astro-arc-generate --if-due` · **Cost:** free · **Cache:** — · **Knob:** `frequency`

The panel is the scheduler. The bar widget loads it with the shell, so its
5-minute poll runs from login whether or not the panel is ever opened, and it
passes `--if-due`; a manual Regenerate does not. Both take the same `flock`, so
a click can never double-render a period the poll just started. Nothing runs
while you are logged out — see the Schedule caption in the panel.

There used to be a systemd timer as well. On Omarchy the shell is the desktop,
so it only ever covered a session with no shell running, and it outlived the
plugin: after removal it fired into a deleted script every fifteen minutes.

"Already generated?" is decided by re-keying `last-run.json`'s `generatedAt`
under the frequency set **right now**, never by comparing its stored
`periodKey`. That key was filed under whatever frequency was set at the time,
so comparing it directly made switching Daily → Monthly start a paid render
as the immediate consequence of changing a dropdown.

Period keys: `YYYY-MM-DDTHH` · `YYYY-MM-DD` · ISO `%G-W%V` · `YYYY-MM`.

## 1. Compute the chart

**Runs:** `astro_engine.py` · **Cost:** free · **Cache:** — · **Knob:** birth date, time, place

[Astronomy Engine](https://github.com/cosinekitty/astronomy), vendored as one
file, gives natal placements and today's transits to about an arcminute;
`ephemeris.py` adds Placidus houses on top of it. It replaced pyswisseph, which
needed compiling on every install, after the two were compared over 1900–2100
and across four birth charts: no sign and no retrograde flag ever differed, and
a full year of the pipeline's own outputs moved on only a handful of days where
an aspect sat within an arcminute of its orb limit. It emits an `arc`: the moon sign
and phase, the dominant transit, and a **texture** block — polarity,
intensity, exposure, multiplicity — which is the raw material for step 3.

Hourly has no branch here on purpose: transits barely move in an hour, so an
hourly schedule shares the daily arc, and the day's reading and meaning, and
re-rolls only the material and the picture.

## 2. Stage 1 — the reading

**Runs:** `llm_pipeline.stage1_interpret` · **Cost:** ~$0.0050 · **Cache:** per period + chart · **Knob:** `stage1Model`

One LLM call turns the chart into a psychological reading in a Jungian frame,
plus a one-line distillation and a narrative position. It is told, forcefully,
never to use visual language — that firewall is what stops the reading from
quietly dictating the picture.

Cached in `pipeline/readings/<periodKey>.json`, invalidated by
`STAGE1_SCHEMA_VERSION`. **Editing the prompt does not invalidate the cache** —
bump the schema version, or you will test yesterday's output.

## 3. The dial — composition, without an LLM

**Runs:** `llm_pipeline.dial_from_texture` · **Cost:** free · **Cache:** — · **Knob:** — always follows the chart

The day's texture becomes hard numbers and fixed sentences: how many sites
(1–3), how many objects (2–3 on a quiet day, 4–6 on a crowded one), the word
budget (60–130), which verbs, whether the two registers **collide** (hard days)
or **cohere** (soft ones), how **prominent** the highlight is (small and to one
side, plainly present, or dominating the frame), in what temper, and how the
actor treats the highlight, the objects and the place. None of it says what
anything *is* — that is the charter's job.

The **light** comes from here too, from the exposure band, and it is written
as a failure condition rather than a mood: a low-exposure day must be night or
somewhere daylight does not reach, with at least two thirds of the frame dark;
a high-exposure day must be full, even daylight. As moods ("most of the frame
is unlit"), neither was obeyed. Mood words are not obeyed; counts and failure
conditions are.

**Then the style draws.** `draw_style_choices` takes the dial and fills in what
each world says for itself, from arrays in the style's charter. Each draw skips
whatever this style drew in its last two runs, and is seeded from the period,
the style and the history length, so a sweep is reproducible but a same-day
regenerate differs:

- **verbs**: 3 process verbs (things happening with no one doing them) from
  the day's polarity, and an **activity** (work, rest, play, ritual or
  travel; none repeats until the others have had a turn) with 3 actor verbs
  from it. Before this, every style got one fixed list, and spill, feed and
  bear down sat in a third to a half of all prompts, with 83% of actors at a
  chore.
- **space**: one depth recipe, replacing the fixed "half the frame open" that
  put far off / distance / beyond into 29 of 33 days. Every style also gets the
  same WALLPAPER rule: somewhere for the eye to go past the subject.
- **highlight mode**: today's kind of wrongness for the highlight.
- **condition**: how worn things are (newly made, kept, worn, failing,
  ruined), weighted toward the far end on hard days. Wear used to be required
  in five places at once, and every image came back weathered.
- **light**: the exposure band's line comes from the style's own `[light]`
  table (`lo` / `mid` / `hi`), required in every style. There are no global
  light lines: an Earth sky is wrong for cosmic, and a dusk-only palette needs
  to say what a bright day looks like in its world.

The draws go into the history entry and are shown on the run page.

A reading with no texture block is an error, not a neutral default: there is
no fallback dial. (`NO_TEXTURE_DIAL` was deleted in October 2026, and a
`pipelineMode: "legacy"` setting that pinned the dial was removed on
2026-09-13.)

## 4. Registers — what the scene is made of

**Runs:** `llm_pipeline.pick_registers` · **Cost:** free · **Cache:** — · **Knob:** `registers.toml`

Each image gets a **primary** register (the protagonist material) and usually
a **secondary**, from 20 registers in five families: living, mineral, built,
fluid, made. On a collide day the secondary comes from a different family; on a
cohere day, from the same one. The primary rotates away from anything used in
the last 5 days (the first 10 runs simply cycle the list, for coverage).

Registers are picked **before** Stage 1.5, because they are one of its inputs.
Until 2026-09-26 they were picked after the scene had already been cast, so a
register could only reach the prompt as an adjective: with the reading pinned,
20 registers came back as one scene.

Every style says what each of the 20 registers means in its world, in its
`[registers]` table; that line goes to steps 6 and 7. A missing line is a
validation error, not a silent gap.

## 5. Stage 1.5 — the meaning

**Runs:** `llm_pipeline.stage15_meaning` · **Cost:** ~$0.0059 · **Cache:** per period + chart + style · **Knob:** `stage1Model`

The day's myth. Turns the reading into an archetypal constellation, a
movement, a felt quality, and the **actor** the constellation already contains
— who is in this situation, and which of the 11 kinds in `actors.toml` it is
(solitary, crowd, animal, swarm, presence, the dead, none and so on).

The constellations an install has used recently are passed in as already
spent, and so are the last 4 actor kinds. Without that pressure, 83% of days
reached for the same archetype and most actors were `solitary`.

Jung and Neumann say what kind of situation the day is. A charter's `myth`
field says which traditions its stories come from (Shinto kami and yōkai,
loa in hardware, the constellations as beings), and the meaning is named in
that world's terms where one fits. The actor is named as who they are, not
what they are doing; the activity is drawn later.

Cached in `pipeline/amplifications/<periodKey>.json`, invalidated by
`STAGE15_SCHEMA_VERSION` and keyed on a hash of the style's `[world]` table, so
**a regenerate never changes the day's meaning**, and editing the world does.
`presence` is only eligible in a style whose charter says how the numinous
takes a body.

## 6. Stage 1.5 — the material

**Runs:** `llm_pipeline.stage15_material` · **Cost:** ~$0.0059 · **Cache:** never · **Knob:** `stage1Model`

The furniture of that myth, built from the day's register: **2–6 concrete
objects** and one **highlight** — the thing the eye goes to first. At least half
the objects, and the highlight, must be made of the primary register's
substance.

**What a highlight is depends on the world.** Each charter defines it in its
`highlight` field, and a register line may say what form it takes in that
material. Ghibli's is the strange thing nobody remarks on; cyberpunk's is
something alive in the machine; cosmic's is a constellation-being's body. Each
charter's `highlight_modes` list the kinds of wrongness it can have, and one is
drawn per run. No definition names a size comparison any more: "the size of a
barn" put barn into 21 of 33 days. Until 2026-10-09 this was "the anomaly",
defined once for every style; the data key is still `anomaly`.

The last 6 runs' objects and highlights are passed in as spent, and the
day's condition is passed in so the objects do not arrive pre-weathered.

Never cached, so every run, regenerates included, gets a new register and new
objects for the same meaning. That is what keeps same-day regenerates from all
looking alike. The highlight is mandatory, and it is never flagged in the
prompt with words like "out of place"; naming it out loud made it read as a
bolted-on item.

## 7. Stage 2 — the image prompt

**Runs:** `llm_pipeline.stage2_image_prompt` · **Cost:** ~$0.0139 · **Cache:** never (temp 0.95) · **Knob:** `stage2Model`, `artStyle`

Everything above converges here: the reading, the meaning and its actor, the
objects and the highlight with its definition and prominence, both registers with their style lines, the dial's
composition brief, the style's world, the concept tags of the last 8
generations to avoid, and any hard-blocked terms. It is **not** given the
style's label: "Studio Ghibli" is a dropdown name, and the world charter alone
decides the scene (a prompts-only comparison over 7 styles found no studio,
artist or film names either way, and no world lost).

Its rules, in short: describe only what is physically there, never what it
means; concrete and oddly specific over generically mystical; things must be
doing something to each other; each register must contribute a substantial
named thing of its own. Colour comes from each scene's own materials and light
from the dial — there is no fixed palette for the series. (A natal "visual
signature" that tried to fix one was removed on 2026-10-08: with and without
it, the prompts differed no more than two draws with it.)

**The style decides the world, not just the look.** All seven styles have a
charter in its `styles/<key>.toml` (era, materials, physics, society, weather,
inhabitants, strangeness, the numinous, dread, favoured actors, myth,
highlight) and their own **brief rules**, phrased as failure conditions
because that is what Stage 2 obeys. Every style has a **signature** rule:
something in frame that could only exist in this world, and an image that
could be relabelled as another style fails. The division Stage 2 is given:
the registers decide what things are made of; the world decides what place
this is and what lives there. See `skills/astro-arc-style/SKILL.md` for every field.
There is no fallback: a style with a missing or invalid field fails loudly,
naming the style and the field, and never generates (see [Styles are
files](#styles-are-files)).

## 8. The melodrama check

**Runs:** `llm_pipeline.caption_check` · **Cost:** ~$0.0001 · **Cache:** never · **Knob:** `captionModel` (default `gpt-4o-mini`)

A cheap judge files the prompt the way a stock library would and answers three
questions: does it show **motive**, does it take **sides**, is any face or pose
**performed** for the viewer. Force, struggle and death are allowed; telling the
viewer what to feel is not. It was tuned against 22 hand-labelled scenes
(`melodrama-calibration.toml`, 21/21 agreement).

A failure resamples step 7, up to twice, which costs a chat call rather than a
render. If every attempt fails the last prompt ships anyway and the failure is
recorded in the run's `pipeline-meta` (`captionCheck`, one row per attempt) — a
strict judge must never cost the day its image.

## 9. Render

**Runs:** `openai_image_gen.py` · **Cost:** ~$0.042 · **Cache:** — · **Knob:** `openaiModel`, `openaiQuality`

The style suffix is appended by `final_image_prompt()` — its technique, plus,
in the styles that define `suffix_figures` (ghibli, cyberpunk), how that style
draws people when a figure is in frame — and the prompt is rendered at the
configured tier, every image, people or not. Production, `sweep.py` and every
other harness join the prompt through that one function, so a test sends
exactly what a real run sends.

The model is sent `moderation: "low"` where it accepts it, and a **safety
refusal is retried** up to twice with the identical request. Refusals are not
deterministic — the same prompt has been refused twice and accepted once — and
a refused request is not billed. Any other error fails at once.

## 10. Palette

**Runs:** `palette_extract.py` · **Cost:** free · **Cache:** — · **Knob:** `themeGenerator`

Derives `colors.toml` from the rendered image, so the colors and the wallpaper
always agree. Set `themeGenerator: "aether"` to hand this to Omarchy's own
generator instead.

## 11. Record

**Runs:** `build_review.py` · **Cost:** free · **Cache:** — · **Knob:** `historyRetentionDays`

The run enters `history.json` only now, after the image exists: a refused or
failed render no longer moves the register rotation or the avoided-concepts
window on.

Then it is filed in the archive, `~/.local/share/astro-arc/`: a page with the
image, the reading and every dial value, the complete theme (so it can be saved or exported later), and a thumbnail. Then the
gallery, `index.html`, is rewritten, newest first. The archive is the user's
data, not state, so it lives where removing the plugin cannot touch it.
`astro-arc-prune-history` then drops entries past the retention window, never
the newest.

## 12. Apply

**Runs:** `omarchy-theme-bg-set`, or `omarchy-theme-set` · **Cost:** free · **Cache:** — · **Knob:** `backgroundOnly`

By default only the wallpaper changes, and whatever theme you already run keeps
its colors. The wallpaper is the archived file itself, which is why this runs
after step 11. Turn `backgroundOnly` off and the palette from step 10 is
applied as the `astro-arc` theme too.

---

## Styles are files

**Runs:** `Catalog.load()`, `catalog.validate()` · **Cost:** free · **Knob:** `artStyle`, `~/.config/astro-arc/styles/`

A style is one self-contained file: `<key>.toml` plus a required `<key>.jpg`
preview. Everything about how a world looks lives in that file (its world
charter, brief rules, a line per register, verbs, depth recipes, highlight
modes, `[light]` and the technique suffix). Everything that holds for every
style (the meaning machinery, the wallpaper rule, exposure bands, registers,
actor kinds, the wear scale) lives once in the pipeline. No style-specific
field exists outside a style's file, and there are no neutral fallbacks.

`Catalog.load()` reads two folders, once at the start of a run:

| Folder | What is there |
| --- | --- |
| `backend/pipeline/styles/` | the 7 shipped styles |
| `~/.config/astro-arc/styles/` (`$XDG_CONFIG_HOME` respected) | the user's own, untouched by `omarchy plugin update`. A file with a shipped key is ignored with a warning |

Every file goes through `catalog.validate()`. The style list is built
from the result: shipped styles by label, the user's tagged "custom", and an
invalid one as "(Failed Validation)", which cannot generate.

**Making one is a conversation with your own agent, not a form — and the user
approves every step of it.** **+ Create a new style…** at the end of the style
list takes a one-line idea and runs `astro-arc-style agent create`, which opens
the user's Omarchy default agent in a terminal (`omarchy-launch-tui`), briefed
with the `astro-arc-style` skill (`skills/astro-arc-style/SKILL.md`). It does
not go through `omarchy-agent`, which starts agents unattended: Astro-Arc
starts the agent in its mode that asks before each action (`ASKING_MODE` in
`astro-arc-style`: Claude Code, Codex in its read-only sandbox, Gemini, Grok)
and starts no other, so the user approves every command it runs. The skill
has it run each paid command below with `--estimate` first (free: prints the
cost) and ask before spending, and show every file it writes by its full path.
The skill is the schema, the writing rules and the workflow; the agent
follows it:

```sh
astro-arc-style validate <file>  # free; names each bad field
astro-arc-style preview  <file>  # 6 prompts: a hard + a soft day
astro-arc-style meaning  <file>  # last 8 days, blind judge: PASS/FAIL
astro-arc-style render   <file>  # the preview image, your settings
```

`meaning` scores whether hard days still read hard and soft days soft, how
many prompts are near-duplicates, and how often the style falls back on one
favourite scene. With no default agent set, or one that can't be started in an
asking mode, **Copy prompt** builds one
self-contained prompt (the skill plus the idea) for any web chat, and
**Import theme…** brings the TOML back, saves it in the user folder and
validates it. **Fix with AI** / **Copy fix prompt** do the same for a style
that failed validation, with its errors attached. The plugin itself never asks
a model to write a style, and has no API fallback for it.

Nothing is installed into the agents' own folders: the brief names the skill
file. `astro-arc-style install-skill`, run by the user, links it into each
agent's skills folder (`~/.claude`, `~/.codex`, `~/.agents`, `~/.pi/agent`,
`~/.hermes`, where they exist) so any session can use it; it never replaces
anything already there, and `uninstall-skill` removes exactly those links.

---

## Tweak it without touching code

Five TOML files, read fresh on every run. No restart, no reinstall.

| File | What it controls | Try this |
| --- | --- | --- |
| `pipeline/registers.toml` | the 20 materials a scene is built from, in five families | add one under the family it is made of |
| `pipeline/styles/<key>.toml` (+ `<key>.jpg`) | the 7 shipped styles, one file each (your own: `~/.config/astro-arc/styles/`, [made with your agent](#styles-are-files)): `label`, `suffix_base` / `suffix_figures` (or `suffix`), and per style a `world` charter, `brief` rules, `registers` lines, `verbs`, `space`, `highlight_modes` and `light` (see `skills/astro-arc-style/SKILL.md`) | edit a charter field, then sweep it and run `term_report.py` |
| `pipeline/actors.toml` | the 11 actor kinds Stage 1.5 casts from, and register/actor pairs to exclude | reword a kind; keep it a span and a principle, never a list |
| `pipeline/cliches.toml` | hard-blocked exact terms | add a word **only after** you see it repeat |
| `pipeline/model-rates.toml` | model prices and the quality presets | add a model, or retune a preset |

`pipeline/melodrama-calibration.toml` is the hand-labelled set the melodrama
check is scored against (`score_melodrama.py`); generation never reads it.

Everything else is one config file at
`~/.local/state/omarchy/settings/astro-arc.json`, all of it settable through
`astro-arc-config` or the panel:

| Knob | Does what |
| --- | --- |
| `frequency` | `hourly` / `daily` / `weekly` / `monthly` / `manual` |
| `intervalHours` | under `hourly`, hours between runs: `1` / `2` / `3` / `4` / `6` / `8` / `12` |
| `artStyle` | which style: a file name (no `.toml`) in either styles folder |
| `stage1Model` / `stage2Model` | the writing models (Stage 1.5 runs on `stage1Model`) |
| `openaiModel` / `openaiQuality` | the image model and tier |
| `captionModel` | the melodrama judge; unset means `gpt-4o-mini`, the model it was calibrated on |
| `maxCostPerImage` | hides image models above this price from the picker; `0` disables |
| `backgroundOnly` | `true` (default): change only the wallpaper. `false`: apply the palette as a theme too |
| `themeGenerator` | `built-in` (palette from the image) or `aether` |
| `historyRetentionDays` | how long generations are kept |

### Two traps

#### Editing a stage prompt does not invalidate its cache

Stage 1 and Stage 1.5's meaning are cached by period and chart. Bump the
matching `*_SCHEMA_VERSION` when you change a prompt, or you will be reading
yesterday's answer and concluding your edit did nothing. The material is never
cached, so a material-prompt edit shows up on the next run.

#### A sweep that pins one dial cell overstates a template

`sweep.py --cell` holds one cell for every sample. That is right for isolating
a register, but the cell's own sentences (a soft/mid cell says the actor
*tends* things, in a familiar place) then repeat across every sample and read as
a style problem. Production rotates cells daily. When judging variety, rotate
cells across registers.

### Adding a register is one line in one file

Put its name under the family it is made of in `registers.toml` and you are
done — the rotation list and the family lookup are derived from that. For a
world style, add a line for it under `[registers]` too, or it reaches
that world by name alone.

### Change something and measure it

```sh
sweep.py --out my-test --registers all --pairs           # every register, one fixed reading
sweep.py --out wording --registers botanical --no-image  # prompts only, cents
sweep.py --out dark --cell hard/hi/4/lo --style cyberpunk # pin one dial cell
```

`sweep.py` pins the reading and the dial so the only thing varying is the thing
under test. Like production, it draws the day's meaning once and the material
per register, renders at the configured tier, and records the melodrama
verdict (without resampling, so it shows how often production would). It
touches no production state — your history, caches, last run, archive and live
theme are left alone. Output lands in `state/verification/<name>/` with every
prompt, cost, render and a CSV.

Use it. This project's own history records that 2–4-sample spot checks let the
same content bug ship three times, and that the one comparison batch which
would have caught it was never saved.

### Test a fresh install without touching your own

There is no install step, and every path derives from `$HOME` (the archive from
`$XDG_DATA_HOME`, defaulting under it), so a throwaway home is a genuinely new
user in seconds:

```sh
FRESH=$(mktemp -d)
mkdir -p $FRESH/.config/omarchy/plugins
cp -a . $FRESH/.config/omarchy/plugins/garrett.astro-arc
HOME=$FRESH $FRESH/.config/omarchy/plugins/garrett.astro-arc/backend/bin/astro-arc-generate
```

That exercises the real defaults a new user gets and the real first-run paths —
no birth data, no archive, no caches — which is where fresh-install bugs live.

Two things it cannot cover, because they are not `$HOME`-scoped: the QML widget
needs the running shell, and `secret-tool` talks to your real keyring, so a
throwaway home still finds your real API key.

---

## What the complexity pass found (2026-09-13)

### Removed

- **`symbol_map.py`** — 189 lines of fixed-vocabulary image generation,
  superseded by the LLM pipeline. Nothing imported it; every remaining mention
  was prose in a comment.
- **The FIGURE HIERARCHY rule** in `STAGE2_SYSTEM`, which capped how many faces
  could turn toward the viewer. Removed against measured evidence — forced rows
  of six and nine equally-sized faces rendered cleanly at 3× zoom — with its
  provenance kept in a comment saying plainly that the finding is about *one
  image model at one quality tier*, so a model swap re-opens the question
  instead of inheriting the answer.

### Fixed — three things a fork would have hit

- `openaiModel` and `openaiQuality` default to `""`, and `jq`'s `//` only
  substitutes for `null` — so a config where the image model had never been
  picked passed an *empty model name* to the API instead of falling back.
- `maxCostPerRun` had no setter for its entire life, despite the generate
  script telling you to raise it. Given one — then removed entirely later the
  same day, along with the quality bump it existed to gate.
- A comment in `openai_image_gen.py` asserted the safety classifier is
  deterministic and that retrying is always wasted spend. Both are false,
  measured — and that comment was steering a real decision.

### Left alone, on purpose

- **`cliches.toml` is empty by design.** Pre-seeding a blocklist from guesses
  gives false confidence and decays as the model finds synonyms for whatever
  you guessed instead.
- **`image_gen.py`** (local Stable Diffusion) is no longer offered in the UI but
  still works if set by hand.
- **The `intrusion`/`anomaly` alias** keeps old `pipeline-meta-*.json` files
  rendering.

### Closed — the four judgment calls, all resolved

- **Period-key logic lived in four implementations.** Now one,
  `period_key.py`, plus `Model.js`'s — which must exist because QML cannot call
  Python inside a property binding. `period_key.py --selftest` checks them
  against one table of cases, including the ISO week-year edges, and runs the
  JS through node when node is present.
- **`pipelineMode: legacy` removed.** The dial always follows the chart.
- **The people quality bump removed**, and with it `maxCostPerRun`, two cost
  estimator CLI shims, and ~73 lines of budget projection.
- **`imageBackend` now defaults to `"openai"`**, matching `provider`.
