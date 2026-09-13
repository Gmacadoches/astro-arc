# How one image gets made

One generation turns a birth chart and today's sky into a wallpaper and a
matching desktop palette. A fresh one costs about **$0.027** at the High
preset; a same-day re-run is about **$0.019**, because the reading is cached
and only the picture is remade. The render itself is the slow part, around 40
seconds.

This document walks the whole path in order. Every step names the file that
runs it, what it reads, what it writes, whether it is cached, and **the one
knob that changes it** — so you can retune this thing without reading any
Python.

If you only read one section, read [Tweak it without touching
code](#tweak-it-without-touching-code).

---

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
 10  APPLY       omarchy-theme-set                 wallpaper + palette go live
                                          │
 11  RECORD      build_review.py                   browsable entry + cost log
```

Steps 2–7 are the interesting part and all live in one file. Steps 0, 1 and
8–11 are plumbing.

---

## 0. Decide whether to run at all

**Runs:** `astro-arc-generate --if-due` · **Knob:** `frequency`

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

**Runs:** `astro_engine.py` · **Cost:** free · **Knob:** birth date/time/place

Swiss Ephemeris (bundled Moshier — no data files to install) gives natal
placements, houses, and today's transits. It emits an `arc`: the moon sign
and phase, the dominant transit, and a **texture** block — polarity,
intensity, exposure, multiplicity — which is the raw material for step 3.

Hourly has no branch here on purpose: transits barely move in an hour, so an
hourly schedule shares the daily arc and re-rolls only the picture.

## 2. Stage 1 — the reading

**Runs:** `llm_pipeline.stage1_interpret` · **~$0.0031** · **cached per
period + chart** · **Knob:** `stage1Model`

One LLM call turns the chart into a psychological reading in a Jungian frame,
plus a one-line distillation and a narrative position. It is told, forcefully,
never to use visual language — that firewall is what stops the reading from
quietly dictating the picture.

Cached in `pipeline/readings/<periodKey>.json`, invalidated by
`STAGE1_SCHEMA_VERSION`. **Editing the prompt does not invalidate the cache** —
bump the schema version, or you will test yesterday's output.

## 3. The dial — composition, without an LLM

**Runs:** `llm_pipeline.dial_from_texture` · **free** · **Knob:**
`pipelineMode`

The day's texture becomes hard numbers: how many sites, how many objects, the
word budget, which verbs, whether the two registers **collide** or **cohere**,
how much spatial pressure, how much light. These used to be constants, which
is why every image once carried the same implicit meaning however the chart
moved.

`pipelineMode: "coherent"` lets the chart drive all of it. `"legacy"` pins it
to the old constants.

## 4. Stage 1.5 — amplification

**Runs:** `llm_pipeline.stage15_amplify` · **~$0.0051** · **cached per period
+ chart** · **Knob:** `stage1Model` (deliberately shares Stage 1's)

Turns the reading into an archetypal constellation, a movement, **4–7 concrete
objects**, a felt quality, and an **anomaly** — one thing that belongs to the
scene's world but is wrong in exactly one way (far out of scale, or far older
and more ruined than everything around it). The anomaly is mandatory and must
never be flagged as strange in the prompt; naming the wrongness out loud is
what made it read as a bolted-on item.

These objects are **what is in the scene**.

## 5. The visual signature

**Runs:** `llm_pipeline.get_visual_signature` · **~$0.0087** · **cached per
birth chart, effectively forever** · **Knob:** `stage2Model`

One description of light and contrast for the whole series, so every image
reads as the same hand. Stage 2 is explicitly told to honor its light and
contrast and **not** to copy its color words — naming the same palette every
time is what made the series monotonous.

## 6. Registers — what the scene is made of

**Runs:** `llm_pipeline.pick_registers` · **free** · **Knob:**
`pipeline/registers.toml`

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

**Runs:** `llm_pipeline.stage2_image_prompt` · **~$0.0087** · **never cached,
temperature 0.95** · **Knob:** `stage2Model`, `artStyle`

Everything above converges here: reading, objects, anomaly, signature, both
registers, the dial's composition brief, the art style's guidance, the concept
tags of the last 8 generations to avoid, and any hard-blocked terms.

This is the only stage that runs every time, which is why two generations an
hour apart produce different pictures from the same reading.

Its rules, in short: describe only what is physically there, never what it
means; concrete and oddly specific over generically mystical; things must be
doing something to each other; each register must contribute a substantial
named thing of its own.

## 8. Render

**Runs:** `openai_image_gen.py` · **$0.0058 (low) – $0.042 (high)** ·
**Knob:** `openaiModel`, `openaiQuality`, `maxCostPerRun`, `maxCostPerImage`

The style suffix is appended after Stage 2, then the prompt is rendered. When
a person is in frame the quality tier is bumped to `high`, subject to the cost
ceilings — that bump is most of what a people image costs.

A refusal from the safety classifier is retried. That is not wasted spend:
identical prompts have been refused once and accepted on a later attempt.

## 9–11. Palette, apply, record

`palette_extract.py` derives `colors.toml` from the rendered image, so the
colors and the wallpaper always agree (set `themeGenerator: "aether"` to hand
this to Omarchy's own generator instead). `omarchy-theme-set` applies it.
`build_review.py` writes the browsable entry — one self-contained HTML page
per generation with the image, the reading and every dial value — and
snapshots the theme so it can be saved or exported later. Finally
`astro-arc-prune-history` deletes entries past `historyRetentionDays`.

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
| `pipelineMode` | `coherent` (chart drives composition) or `legacy` (fixed) |
| `stage1Model` / `stage2Model` | the writing models |
| `openaiModel` / `openaiQuality` | the image model and tier |
| `maxCostPerRun` / `maxCostPerImage` | spend ceilings; `0` disables |
| `themeGenerator` | `built-in` (palette from the image) or `aether` |
| `historyRetentionDays` | how long generations are kept |

### Two traps worth knowing

**Editing a stage prompt does not invalidate its cache.** Stage 1, Stage 1.5
and the signature are cached by period and chart. Bump the matching
`*_SCHEMA_VERSION` when you change a prompt, or you will be reading yesterday's
answer and concluding your edit did nothing.

**Adding a register means editing two files.** `registers.toml` for the name,
`REGISTER_FAMILIES` in `llm_pipeline.py` for its material family — a register
missing from the second gets family `None`, and every such register then
coheres with every other one as an invisible pseudo-family. The pipeline warns
about this at import. A register with people in it needs `PEOPLE_REGISTERS`
and the matching `STAGE2_SYSTEM` sentence too.

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

---

## What the complexity pass found (2026-09-13)

**Removed.** `symbol_map.py` — 189 lines of fixed-vocabulary image generation,
replaced by the LLM pipeline long ago. Every remaining mention of it was prose
in a comment; nothing imported it. The FIGURE HIERARCHY rule in
`STAGE2_SYSTEM`, which constrained how many faces could face the viewer, was
removed against measured evidence and its provenance left in a comment above
the prompt.

**Fixed.** Three things a fork would have hit:

- `openaiModel` and `openaiQuality` default to `""`, and `jq`'s `//` only
  substitutes for `null` — so a config where the image model had never been
  picked passed an *empty model name* to the API instead of falling back.
- `maxCostPerRun` had no setter for its entire life, despite the generate
  script telling you to raise it. It now has one, and appears in the defaults.
- A comment in `openai_image_gen.py` asserted the safety classifier is
  deterministic and that retrying is always wasted spend. Both are false,
  measured — and that comment was steering a real decision.

**Left alone, on purpose.** `cliches.toml` is empty by design; pre-seeding a
blocklist from guesses gives false confidence and decays as the model finds
synonyms. `image_gen.py` (local Stable Diffusion) is no longer offered in the
UI but still works if set by hand. The `intrusion`/`anomaly` alias keeps old
`pipeline-meta-*.json` files rendering.

**Judgment calls, still open.**

- **Period-key logic exists in five places** — `astro-arc-generate`,
  `Model.js`, `astro_engine.py`, `model_catalog.py`, `Panel.qml` — because
  bash, QML and Python cannot share a function. They are kept in sync by hand
  and by comment. This is the single largest structural risk in the codebase,
  and it has already produced one real bug.
- **`pipelineMode` still defaults to `legacy`**, so a fresh install gets the
  pre-2026-09-12 fixed composition rather than the chart-driven one. That is
  probably the wrong default now.
- **The people quality bump may be counterproductive on the current model.**
  It renders people images at `high`, ~7× the cost — but the 2026-09-12 bakeoff
  found `gpt-image-2.5-sunburst` at `low` *more* prompt-faithful than at
  `high`. Worth re-measuring.
- **`imageBackend` still defaults to `"local"`** while `provider` defaults to
  `"openai"`, and the former is vestigial. Harmless, because `provider` is read
  first, but confusing to read.
