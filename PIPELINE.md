# How one image gets made

One generation turns a birth chart and today's sky into a wallpaper and a
matching desktop palette. A fresh one costs about $0.028 at the High preset; a
same-day re-run is about $0.019, because the reading is cached and only the
picture is remade. The render is the slow part.

**At a glance:** $0.028 fresh run · $0.019 same-day re-run · ~40s render · 25 registers

Twelve steps. Four of them call a language model: the reading, the objects, the visual signature and the prompt. The signature is computed once per chart, so a typical day makes three of those calls, not four. This document walks the whole path
in order, and every step names the file that runs it, what it reads, what it
writes, whether it is cached, and **the one knob that changes it** — so you can
retune this thing without reading any Python.

If you only read one section, read [Tweak it without touching
code](#tweak-it-without-touching-code).

> This file is the source. `docs/pipeline.html` is generated from it by
> `backend/bin/build_pipeline_page.py` — edit here, run that, never edit the
> HTML. `build_pipeline_page.py --check` fails if the two have drifted.

---

<!-- page:skip -->
## The map

```
  SCHEDULER  systemd timer + panel poll ──┐
                                          ▼
  0  DECIDE      astro-arc-generate --if-due      is this period already done?
                                          │
  1  CHART       astro_engine.py                  swisseph → planets, houses, texture
                                          │
  ╭───────────────── llm_pipeline.py ──────────────────────────────────╮
  │ 2  READING     Stage 1      what the sky means for this person     │  cached / day
  │ 3  DIAL        (no LLM)     how dense, how lit, how many objects   │  free
  │ 4  OBJECTS     Stage 1.5    4–7 concrete things to build from      │  cached / day
  │ 5  SIGNATURE   one-off      the series' light and contrast         │  cached / chart
  │ 6  REGISTERS   (no LLM)     what the scene is MADE of              │  free
  │ 7  PROMPT      Stage 2      the actual image prompt                │  never cached
  ╰────────────────────────────────────────────────────────────────────╯
                                          │
  8  RENDER      openai_image_gen.py               prompt → PNG
                                          │
  9  PALETTE     palette_extract.py                PNG → colors.toml
                                          │
 10  APPLY       omarchy-theme-bg-set              wallpaper goes live (+ palette if opted in)
                                          │
 11  RECORD      build_review.py                   browsable entry + cost log
```

Steps 2–7 are the interesting part and all live in one file. Steps 0, 1 and
8–11 are plumbing.

---

## The pipeline

Steps 2–7 all live in one file, `llm_pipeline.py`, and are where the picture is
actually decided. Everything else is plumbing. The steps that spend money are
marked.

## 0. Decide whether to run at all

**Runs:** `astro-arc-generate --if-due` · **Cost:** free · **Cache:** — · **Knob:** `frequency`

Two independent triggers fire this, deliberately: a systemd user timer every
15 minutes, and the panel's own 5-minute poll. Both pass `--if-due`, both hit
the same `flock`, so whichever notices a new period first wins and the other
is a no-op. Neither can outlive your login session — see the Schedule caption
in the panel.

"Already generated?" is decided by re-keying `last-run.json`'s `generatedAt`
under the frequency set **right now**, never by comparing its stored
`periodKey`. That key was filed under whatever frequency was set at the time,
so comparing it directly made switching Daily → Monthly start a paid render
as the immediate consequence of changing a dropdown.

Period keys: `YYYY-MM-DDTHH` · `YYYY-MM-DD` · ISO `%G-W%V` · `YYYY-MM`.

## 1. Compute the chart

**Runs:** `astro_engine.py` · **Cost:** free · **Cache:** — · **Knob:** birth date, time, place

[Swiss Ephemeris](https://www.astro.com/swisseph/) (bundled Moshier, no data
files to install) gives natal
placements, houses, and today's transits. It emits an `arc`: the moon sign
and phase, the dominant transit, and a **texture** block — polarity,
intensity, exposure, multiplicity — which is the raw material for step 3.

Hourly has no branch here on purpose: transits barely move in an hour, so an
hourly schedule shares the daily arc and re-rolls only the picture.

## 2. Stage 1 — the reading

**Runs:** `llm_pipeline.stage1_interpret` · **Cost:** ~$0.0031 · **Cache:** per period + chart · **Knob:** `stage1Model`

One LLM call turns the chart into a psychological reading in a Jungian frame,
plus a one-line distillation and a narrative position. It is told, forcefully,
never to use visual language — that firewall is what stops the reading from
quietly dictating the picture.

Cached in `pipeline/readings/<periodKey>.json`, invalidated by
`STAGE1_SCHEMA_VERSION`. **Editing the prompt does not invalidate the cache** —
bump the schema version, or you will test yesterday's output.

## 3. The dial — composition, without an LLM

**Runs:** `llm_pipeline.dial_from_texture` · **Cost:** free · **Cache:** — · **Knob:** — always follows the chart

The day's texture becomes hard numbers: how many sites, how many objects, the
word budget, which verbs, whether the two registers **collide** or **cohere**,
how much spatial pressure, how much light. These used to be constants, which
is why every image once carried the same implicit meaning however the chart
moved.

The dial always follows the chart. A `pipelineMode: "legacy"` setting used to
pin it back to fixed constants — an option to switch off the mechanism this
whole project is built on, and it was the default. Removed 2026-09-13. The
constants survive as `NO_TEXTURE_DIAL`, the fallback for a reading that has
no texture block at all.

## 4. Stage 1.5 — amplification

**Runs:** `llm_pipeline.stage15_amplify` · **Cost:** ~$0.0051 · **Cache:** per period + chart · **Knob:** `stage1Model`

Turns the reading into an archetypal constellation, a movement, **4–7 concrete
objects**, a felt quality, and an **anomaly** — one thing that belongs to the
scene's world but is wrong in exactly one way (far out of scale, or far older
and more ruined than everything around it). The anomaly is mandatory and must
never be flagged as strange in the prompt; naming the wrongness out loud is
what made it read as a bolted-on item.

These objects are **what is in the scene**.

## 5. The visual signature

**Runs:** `llm_pipeline.get_visual_signature` · **Cost:** ~$0.0087 · **Cache:** per chart, ~forever · **Knob:** `stage2Model`

One description of light and contrast for the whole series, so every image
reads as the same hand. Stage 2 is explicitly told to honor its light and
contrast and **not** to copy its color words — naming the same palette every
time is what made the series monotonous.

## 6. Registers — what the scene is made of

**Runs:** `llm_pipeline.pick_registers` · **Cost:** free · **Cache:** — · **Knob:** `registers.toml`

Each image gets a **primary** register (the protagonist material) and usually
a **secondary**. The dial decides whether they collide or cohere. Registers
rotate by recency across a 5-day window, so the series moves between regions
of image-space rather than merely avoiding recent words.

The amplification objects are what is *in* the scene; the registers are what
the scene is *made of*.

Five of the 25 registers are people registers, and they alone decide whether
figures appear. This is gated on the **primary** only — a human register
arriving as a secondary would inject people into a material-led scene through
a side door.

## 7. Stage 2 — the image prompt

**Runs:** `llm_pipeline.stage2_image_prompt` · **Cost:** ~$0.0087 · **Cache:** never (temp 0.95) · **Knob:** `stage2Model`, `artStyle`

Everything above converges here: reading, objects, anomaly, signature, both
registers, the dial's composition brief, the art style's guidance, the concept
tags of the last 8 generations to avoid, and any hard-blocked terms.

This is the only stage that runs every time, which is why two generations an
hour apart produce different pictures from the same reading.

Its rules, in short: describe only what is physically there, never what it
means; concrete and oddly specific over generically mystical; things must be
doing something to each other; each register must contribute a substantial
named thing of its own.

**And every image must be open.** At least half the frame is sky, horizon,
landscape, sea or a distance that keeps going, and the viewpoint is pulled back
far enough for that to be true. An interior is allowed only if it opens onto
that distance through a large window, a doorway, a missing wall. A sealed room
is a failed response.

These are wallpapers — they sit behind windows all day, and an enclosed room
reads as claustrophobic at that size however good its contents are. The rule
lives in the composition brief rather than the system prompt because the brief
is the part that gets obeyed, and it is stated as a share of the frame because
"make it feel open" is a mood word, and mood words are not obeyed.

## 8. Render

**Runs:** `openai_image_gen.py` · **Cost:** ~$0.0058 · **Cache:** — · **Knob:** `openaiModel`, `openaiQuality`

The style suffix is appended after Stage 2, then the prompt is rendered at the
configured tier — every image, people or not. A bump to `high` for people
images was removed on 2026-09-13: the bakeoff found this model *more*
prompt-faithful at `low` than at `high`, for a seventh of the price.

A refusal from the safety classifier is retried. That is not wasted spend:
identical prompts have been refused once and accepted on a later attempt.

## 9. Palette

**Runs:** `palette_extract.py` · **Cost:** free · **Cache:** — · **Knob:** `themeGenerator`

Derives `colors.toml` from the rendered image, so the colors and the wallpaper
always agree. Set `themeGenerator: "aether"` to hand this to Omarchy's own
generator instead.

## 10. Apply

**Runs:** `omarchy-theme-bg-set`, or `omarchy-theme-set` · **Cost:** free · **Cache:** — · **Knob:** `backgroundOnly`

By default only the wallpaper changes, and whatever theme you already run keeps
its colors. Turn `backgroundOnly` off and the palette from step 9 is applied as
the `astro-arc` theme too. The palette is derived either way, so every review
entry can still be saved or exported as a full theme.

## 11. Record

**Runs:** `build_review.py` · **Cost:** free · **Cache:** — · **Knob:** `historyRetentionDays`

Writes the browsable entry — one self-contained HTML page per generation, with
the image, the reading and every dial value — and snapshots the theme so it can
be saved or exported later. Then `astro-arc-prune-history` deletes entries past
the retention window.

---

## Tweak it without touching code

Four TOML files, read fresh on every run. No restart, no reinstall.

| File | What it controls | Try this |
| --- | --- | --- |
| `pipeline/registers.toml` | the 25 materials a scene is built from | add one; drop one that never lands |
| `pipeline/styles.toml` | the 7 art styles | add a preset with `label`, `suffix`, `guidance` |
| `pipeline/cliches.toml` | hard-blocked exact terms | add a word **only after** you see it repeat |
| `pipeline/model-rates.toml` | model prices and the quality presets | add a model, or retune a preset |

Everything else is one config file at
`~/.local/state/omarchy/settings/astro-arc.json`, all of it settable through
`astro-arc-config` or the panel:

| Knob | Does what |
| --- | --- |
| `frequency` | `hourly` / `daily` / `weekly` / `monthly` / `manual` |
| `artStyle` | which styles.toml preset |
| `stage1Model` / `stage2Model` | the writing models |
| `openaiModel` / `openaiQuality` | the image model and tier |
| `maxCostPerImage` | hides image models above this price from the picker; `0` disables |
| `backgroundOnly` | `true` (default): change only the wallpaper. `false`: apply the palette as a theme too |
| `themeGenerator` | `built-in` (palette from the image) or `aether` |
| `historyRetentionDays` | how long generations are kept |

### One trap, and one thing that used to be one

#### Editing a stage prompt does not invalidate its cache

Stage 1, Stage 1.5 and the signature are cached by period and chart. Bump the
matching `*_SCHEMA_VERSION` when you change a prompt, or you will be reading
yesterday's answer and concluding your edit did nothing.

#### Adding a register is now one line in one file

Put its name under the family it is made of in `registers.toml` and you are
done — the rotation list, the family lookup, the people gate and the sentence
Stage 2 reads are all derived from that. Put it under `human` and it is gated
as people automatically. This used to take three edits across two files, two of
which failed silently.

### Change something and measure it

```sh
sweep.py --out my-test --registers all --pairs        # every register, one fixed reading
sweep.py --out wording --registers botanical --no-image  # prompts only, free
```

`sweep.py` pins the reading, the signature and the dial so the only thing
varying is the thing under test, and it touches no production state — your
history, caches, last run and live theme are left alone. Output lands in
`state/verification/<name>/` with every prompt, cost and render.

Use it. This project's own history records that 2–4-sample spot checks let the
same content bug ship three times, and that the one comparison batch which
would have caught it was never saved.

### Test a fresh install without touching your own

Every path in this project derives from `$HOME`, so a throwaway one gives you a
genuinely new install in seconds, with no container and nothing to clean up but
a directory:

```sh
FRESH=$(mktemp -d)
git clone . $FRESH/.config/omarchy/plugins/astro-arc
HOME=$FRESH $FRESH/.config/omarchy/plugins/astro-arc/install.sh
HOME=$FRESH $FRESH/.config/omarchy/plugins/astro-arc/backend/bin/astro-arc-generate
```

That exercises the real installer, the real defaults a new user gets, and the
real first-run paths — the venv, the birth data, the API key, all absent, which
is exactly where fresh-install bugs live. It found five on 2026-09-13, the last
of them only after the install itself was restructured.

Two things it cannot cover, because they are not `$HOME`-scoped: the QML widget
needs the running shell, and `secret-tool` talks to your real keyring, so a
throwaway home still finds your real API key. Docker buys you only the
OS-level dependency question (`jq`, `python3`, `secret-tool` present on a clean
Arch) and costs an image build to ask it.

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
