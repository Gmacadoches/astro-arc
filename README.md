# Astro-Arc

**Your desktop, generated from your birth chart — every day.**

Astro-Arc is an [Omarchy](https://omarchy.org) bar-widget plugin. It reads your
natal astrology chart and the day's transits, turns them into a psychological
reading, turns that into a single image, renders it, and derives a matching
colour palette from the rendered image — so the wallpaper and the theme always
agree, because the theme is made *from* the wallpaper.

It runs on a cadence you pick, paints in one of seven built-in worlds **or one
you make yourself** (your Omarchy agent writes it with you from a one-line
idea), and every generation is browsable, exportable and shareable.

![A night procession of umbrella yōkai and paper-limbed spirits carries a dismantled grain hoist across a bridge in slanting rain, while a road-worn warrior drinks tea on a post-station veranda beside a festival puppet wagon](docs/images/hero.jpg)

---

## Seven worlds, not seven filters

Every style is a **world** with its own era, materials, physics, inhabitants,
myths and rules: what lives there, how it goes dark, what the eye should find
first, and the one thing that marks a picture as unmistakably that world. The
same day's reading, rendered in each:

| Studio Ghibli | Cyberpunk | Cosmic |
| --- | --- | --- |
| ![A river god in work clothes wades past a beached handcart, dragging a net of washbasins and stove lids](docs/images/styles/ghibli.jpg) | ![A carp with a camera lens grown into its skull noses a storm-drain grate while a junction-box spirit receives incense](docs/images/styles/cyberpunk.jpg) | ![The constellation Cancer as a world-sized crab of rock and hull plate over a floating island, its water pouring off the edge into violet space](docs/images/styles/cosmic.jpg) |
| **Antique Engraving** | **Ukiyo-e** | **Symbolist** |
| ![A giant horseshoe crab entered as a specimen, with numbered stars and a graticule running from the plate's edge into the harbour](docs/images/styles/engraving.jpg) | ![A huge ghost fishmonger climbs from boat to boat over a river ferry, a warrior watching fireworks beneath a barge canopy](docs/images/styles/ukiyoe.jpg) | ![A household of cloaked women feed white waterbirds beside a wooden chamber that pours like a throat into a moonlit garden](docs/images/styles/symbolist.jpg) |
| **Illuminated Manuscript** | | |
| ![A pilgrim leads a mule past a pelican larger than a horse, opening its breast over its young, with a snail-knight from the border in the reeds](docs/images/styles/illuminated.jpg) | | |

**[What each world is, and how it was built →](docs/styles.md)** ·
**[Make your own ↓](#make-your-own-world)**

---

## Make your own world

A style is one file, and you don't have to write it. Your Omarchy default agent
writes it with you:

1. Open the style picker in the panel and choose **+ Create a new style…** at
   the bottom of the list.
2. Describe it in one line: *1920s art deco travel posters*, *Dutch Golden Age
   still life*, *1980s arcade cabinet art*.
3. Click **Create with** *your agent* (whichever you set with
   `omarchy default agent`). It opens in a terminal, briefed with the plugin's
   [`astro-arc-style`](skills/astro-arc-style/SKILL.md) skill.

**You stay in charge of every step.** The agent works with you one step at a
time: it writes the world (its era, materials, inhabitants, myths, light and
technique), validates it, test-writes real prompts from your own recent days,
has a blind judge check that hard days still read as hard and soft days as
soft, and renders the preview image. Astro-Arc starts the agent in its mode
that asks you before every command and change — never in an auto-approve,
"yolo" or allow-all mode — and before any step that spends API credit it tells
you the cost and waits for your yes. Every file it writes, renders included,
it shows you by its full path. When it is done, the style is in the list as
**"Your Style · custom"**, and it generates like any built-in one.

Astro-Arc starts **Claude Code, Codex, Gemini and Grok** this way (Codex in its
read-only sandbox, so anything it changes needs your approval). An agent that
can't be started in a mode that asks first isn't started at all; the panel says
so and offers Copy prompt instead.

**No agent set up?** **Copy prompt** puts everything a chat model needs on the
clipboard. Paste it into any chat on the web, then bring the file it writes
back with **Import theme…**, which saves and validates it.

**Not quite right?** With one of your own styles selected, click **Tweak**
beside the picker and say what's off (*fewer landscapes, more character
close-ups*), optionally with today's image as an example. Your agent edits the
style in place, or **Copy prompt** does the same through a web chat. The
current version is saved first, so **Undo last tweak** puts it back.

**Something wrong with it?** A style that fails validation stays in the
dropdown as **(Failed Validation)** and won't generate. **Fix with AI** hands it
back to your agent together with the exact errors, and **Copy fix prompt** does
the same for a web chat.

Your styles live in `~/.config/astro-arc/styles/` as `<key>.toml` plus a
`<key>.jpg` preview. That folder belongs to you, so `omarchy plugin update`
never touches it. To give a style to a friend, send them the two files.

---

## How it works

The chart never reaches the picture directly. It goes through meaning first:

1. **The reading.** Your natal chart and the day's transits become a short
   psychological reading in a Jungian frame, written by a model that is
   forbidden any visual language, so the picture cannot be decided by a cliché
   ("Mars square Moon" never becomes "a red sword").
2. **The myth.** The reading becomes an archetypal situation and the someone or
   something in it, found in the chosen world's own myths: a Ghibli river god, a
   kami living in a junction box, a constellation walking the sky.
3. **The material.** That myth is built out of the day's material register
   (stone, water, paper, machinery and sixteen more) in the world's own terms,
   with one highlight for the eye to find first.
4. **The picture.** A composition brief, a style's rules and its technique turn
   it into an image prompt; the image is rendered and its own colours become the
   desktop palette.

## Why no two days look alike

The chart decides the **shape** of the picture as well as its subject. Four
measurements of the day (how hard the aspects are, how exact, how many, and how
much of the Moon is lit) set how many things are in frame, how many places they
occupy, how close the event sits, whether the two materials collide or cohere,
and how much of the frame is dark. A loaded, hard day is crowded and close; a
quiet one is not.

Then the world draws the rest from its own lists, never repeating what it drew
in its last runs: what is happening to things, what the people are doing (work,
but just as often rest, play, ritual or travel), how the frame shows depth, how
worn things are, and what kind of strangeness the highlight has. The last runs'
objects and myths are passed back in as already spent.

None of this is a filter on a fixed scene. In a 56-image test of all seven
styles on the same two days, no two worlds shared more than 4% of the things
they put in frame.

## Requirements

- **Omarchy.** This is a bar-widget plugin; it does not run standalone.
- **An OpenAI or OpenRouter API key.** Every stage is an API call, so a key is
  required regardless of which models you pick. Pick which one under
  **Provider** in the panel's settings.
- **Optional, to make your own styles:** an Omarchy default agent
  (`omarchy default agent`), or any chat model on the web.

That is all. There is no install step: nothing is compiled, nothing is
downloaded, no service is registered. The backend runs on the Python and
ImageMagick every Omarchy install already has, using the standard library only,
and the chart maths is [Astronomy Engine](https://github.com/cosinekitty/astronomy),
vendored in this repository as a single file. The one exception is optional:
the [moving background](#moving-background), which downloads what it needs
when you ask for it.

## Install

From Omarchy's plugin marketplace, or:

```sh
omarchy plugin add https://github.com/Gmacadoches/astro-arc.git --enable
```

Then click ✦ in the bar, paste your API key, and enter your birth date, time
and place.

Nothing generates on its own until you say so: **Schedule** starts at **None**,
so the first theme is the one you ask for with **Regenerate**. Set it to Hourly,
Daily, Weekly or Monthly once you know what a run costs you — the panel prices
the month for whichever you pick. Hourly gets a second picker for every 1, 2,
3, 4, 6, 8 or 12 hours, and the slots line up with the clock (every 3 hours
runs in the 00:00, 03:00, 06:00 ... slots).

A schedule only advances while you are logged in, because the widget itself is
the scheduler. A locked screen still counts; being switched off, asleep or
logged out does not, and anything that came due during that runs within a few
minutes of your being back rather than being skipped.

**Update:** `omarchy plugin update garrett.astro-arc`. The plugin never
updates itself — the panel shows which release you are on and leaves the
updating to Omarchy.
**Remove:** `omarchy plugin remove garrett.astro-arc`. Everything it made for
you stays, so nothing you care about disappears with it. Delete what you don't
want:

| What | Where |
|---|---|
| Your archive (every generation and its theme) | `~/.local/share/astro-arc/` |
| Your own styles | `~/.config/astro-arc/styles/` |
| Settings | `~/.local/state/omarchy/settings/astro-arc.json` |
| Caches, tweak backups, preview renders | `~/.local/state/omarchy/astro-arc/` |
| API keys | the system keyring: **Remove** beside each key in Advanced settings, or `secret-tool clear service astro-arc` |
| Skill links, only if you made them | run `backend/bin/astro-arc-style uninstall-skill` before removing |

## Your archive

Every generation is kept as a page of its own — the image, the reading, the
prompt and what it cost — together with its complete theme, in
`~/.local/share/astro-arc/`. The image at the top of the panel pages through
them: **‹ ›** (or ← → from the keyboard), the thumbnail strip under it, or
**All** for a grid of everything, filterable by style. Each one comes back with
the reading it was made from.

For the generation showing, **Set wallpaper** puts its colours and wallpaper
back on the desktop. That is temporary by design: the next generation replaces
it. Under **⋯**:

- **Open image** — the full-size wallpaper.
- **Save a copy…** — keep it as a permanent Omarchy theme.
- **Export…** — package the *theme* as a git repo someone can install with
  `omarchy theme install` (the reading is left out).
- **Share…** — the image, the reading, the prompt and the costs as one `.html`
  file that opens anywhere. It asks where to save.
- **Use as style preview** — for one of your own styles.

The whole archive is also one page in your browser
(`~/.local/share/astro-arc/index.html`, newest first — **Open in browser** in
the History grid). Uninstalling the plugin leaves it untouched; delete the
folder when you no longer want it. *History* retention in Advanced settings
sets how long generations are kept (**Clean up…** in the grid goes there); the
newest is never pruned.

## What it costs

Real measured costs, not estimates from a rate card. One preset sets every
model at once:

| Preset | Per run | Per month (daily) |
| --- | ---: | ---: |
| Low | $0.008 | **$0.23** |
| Medium | $0.011 | $0.32 |
| **High** *(recommended)* | $0.031 | **$0.93** |
| Ultra | $0.076 | $2.32 |
| Maximum | $0.210 | $6.39 |

Above roughly $0.05 a run, nothing got measurably better in testing — the upper
tiers buy a stronger *writing* model, not a better picture. Low already produces
a good image.

Figures are measured on this project's own renders and will drift as model
prices change; the widget always shows the live number, what your next click
will charge, and whether that figure is measured or projected.

You can set a hard ceiling per image, and every model your key can reach is
selectable in the Advanced popout if you want to tune it yourself.

## Exporting a generation as an Omarchy theme

Any generation can be exported as a standalone Omarchy theme repository —
palette, wallpaper, preview thumbnail and README. Push it to a git host and
anyone can install it:

```sh
omarchy theme install https://github.com/you/omarchy-your-theme.git
```

Exports deliberately contain no personal content: the palette, the image and the
image prompt, never the reading that produced them.

## Moving background

Turn on **Moving background** under Schedule in Settings and the wallpaper
moves: near things drift slowly against far ones, as if the picture had depth.
It works from a depth map of the image, which a small model ([Depth Anything V2
Small](https://huggingface.co/depth-anything/Depth-Anything-V2-Small),
Apache-2.0) makes in a few seconds on the CPU. Each new generation gets its
own map, and so does any wallpaper you put on screen yourself.

It is off by default, and it needs a one-time install. The first time you turn
it on, the panel lists what it would download and offers **Install (about
270 MB)**. Nothing is downloaded until you press it. It puts two things in
`~/.local/state/omarchy/astro-arc/motion/`:

- the depth model (99 MB), from Hugging Face, pinned to one commit and checked
  against its hash;
- a private Python environment with exactly three packages from PyPI, pinned
  and installed as prebuilt wheels only: onnxruntime 1.31.0, numpy 2.5.4 and
  pillow 13.0.0 (about 170 MB).

It needs no root and touches nothing outside that folder. From a terminal,
`astro-arc-motion plan --venv` shows the same list, and the install is:

```sh
~/.config/omarchy/plugins/astro-arc/backend/bin/astro-arc-motion setup --venv
```

`astro-arc-motion status` says what is in place. Without the install, turning
it on changes nothing: the wallpaper stays still.

It costs little while it runs, and it stops when it would be wasted: on
battery, on the power-saver profile, after two minutes idle, under tiled or
fullscreen windows, while a game runs, and while the screen is locked or off.
Depth maps are deleted on the same schedule as your history. To remove motion
entirely, turn it off and delete the `motion/` folder.

Thanks to [Jampick](https://github.com/jampick), who built it.

## Your data

- **Your API key lives only in the system keyring** (`secret-tool`). Never in a
  file, an environment variable, a command line, or this repo. See
  `backend/bin/astro-arc-apikey`.
- **Your birth data and your readings stay on your machine**: settings in
  `~/.local/state/omarchy/settings/astro-arc.json`, caches in
  `~/.local/state/omarchy/astro-arc/` (safe to delete), your archive in
  `~/.local/share/astro-arc/`. Nothing in this repository contains them.
- Your prompts and chart facts are sent to OpenAI (or OpenRouter, and from
  there to whichever vendor serves the model you picked) to generate each
  image, which is the entire mechanism — if that is not acceptable to you,
  this is not the tool for you. The birth place is looked up with Open-Meteo's
  geocoder, which also supplies its timezone.
- **Your agent, only when you start it.** Creating, fixing or tweaking a style
  with your agent sends what it works on to that agent's model provider: the
  style file, your note, the example image if you tick it, and — while it tests
  a style — prompts built from your recent readings (`preview` and `meaning`
  print them). Nothing goes to an agent unless you click its button; Copy
  prompt keeps it to whichever chat you paste into.

## OpenRouter

OpenRouter puts OpenAI's models and about sixty image models from other vendors
behind one key. Choose it under **Provider** and paste an OpenRouter key
(`sk-or-…`). Each provider keeps its own key in the keyring and remembers its
own model picks, so switching back and forth loses nothing. The first switch
lands on OpenRouter's **High** preset.

Low, Medium and High use cheaper chat models than on OpenAI: DeepSeek V4.1
Flash for Low and Claude Haiku 5.5 for Medium and High, with reasoning turned
off on Low and Medium. On my test runs both wrote better scenes than
gpt-4o-mini for about the same chat cost or less. All three keep Sunburst at
low quality for the image, the same as on OpenAI. Ultra and Maximum are still
the OpenAI tiers.

Models that think before answering bill those reasoning tokens as output, and
on DeepSeek that made one call cost more than a whole gpt-4o-mini run. The
presets set `chatReasoning` for that reason; a hand-picked model keeps its own
default unless you set it with `astro-arc-config --set-chat-reasoning`.

Image models outside OpenAI don't share one quality knob, so the tier is
translated per model:

- **OpenAI models** take `quality` exactly as they do on OpenAI.
- **Models with a resolution setting** (FLUX, Gemini, Qwen, Seedream and
  others) map Low, Medium and High to 1K, 2K and the largest size they offer.
  The dropdown shows which, e.g. "Low (1K)".
- **Grok Imagine** has both and tops out at Medium.
- **Models with neither** (Recraft, MAI) offer one setting.

Prices come from OpenRouter's own model listing, so nothing has to be typed
into `model-rates.toml`. Image models priced per image get an estimate right
away. OpenAI models reuse the token counts measured on your OpenAI runs. The
Gemini image models are priced per token with no published count per image,
so they show "cost known after first render" until you render once. After any
render the figure is the dollars OpenRouter reports billing. OpenRouter's fee
for buying credits isn't in any of these numbers.

## Tuning it

Several tables are plain TOML, read fresh on every run, so an edit takes effect
on the next generation with no restart and no code change. [`PIPELINE.md`](PIPELINE.md)
says which step each one changes:

| File | What it controls |
| --- | --- |
| `~/.config/astro-arc/styles/` | **your own styles**, one `<key>.toml` + `<key>.jpg` each, made [with your agent](#make-your-own-world) and kept across updates |
| `backend/pipeline/styles/` | the seven shipped styles, same format: each one's technique and its world (see [`skills/astro-arc-style/SKILL.md`](skills/astro-arc-style/SKILL.md)) |
| `backend/pipeline/registers.toml` | the 20 material registers a scene is built from |
| `backend/pipeline/actors.toml` | the kinds of actor a scene can have, from a lone person to a swarm |
| `backend/pipeline/model-rates.toml` | model prices, quality presets, shortlists |
| `backend/pipeline/cliches.toml` | imagery to block when it starts recurring (empty by design) |

## Documentation

- [`PIPELINE.md`](PIPELINE.md) — **start here.** How one image gets made, step by
  step, with the config knob that changes each step. Written for someone who
  just forked this and wants to retune it without reading any Python. Also
  published as a page at [`docs/pipeline.html`](docs/pipeline.html), generated
  from that file by `backend/bin/build_pipeline_page.py` — edit the Markdown,
  run the script, never edit the HTML.
- [`RELEASING.md`](RELEASING.md) — how a release is cut (`bin/release`), why
  `master` only ever holds releases, and how updates find them.
- [`docs/styles.md`](docs/styles.md) — the seven worlds, each rendered from
  the same reading and register, with what defines each one.
- [`skills/astro-arc-style/SKILL.md`](skills/astro-arc-style/SKILL.md) — every
  field of a style, what it controls, and how to write one well: the agent
  skill behind "Create a new style", readable by people too. The agent's brief
  points it at this file, so nothing is installed anywhere. If you'd like any
  agent session to know it, run `backend/bin/astro-arc-style install-skill`
  yourself: it links the skill into each installed agent's skills folder
  (`~/.claude/skills/`, `~/.codex/skills/` and so on), never replacing
  anything already there, and `uninstall-skill` removes exactly those links.
- [`docs/decisions/`](docs/decisions/) — why things are the way they are, with
  the measurements behind each call.

Each script carries its own header comment explaining what it does and why it
does it that way; that is the reference for anything PIPELINE.md does not cover.

## Licence

[GNU Affero General Public License v3.0 or later](LICENSE).

Charts are computed with [Astronomy Engine](https://github.com/cosinekitty/astronomy)
by Don Cross, vendored unmodified in `backend/vendor/astronomy/` under the MIT
License. See [`NOTICE`](NOTICE) for the third-party notices.
