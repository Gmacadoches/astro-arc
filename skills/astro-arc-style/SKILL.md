---
name: astro-arc-style
description: >
  Write, fix or check an Astro-Arc theme (a "style" for the Astro-Arc wallpaper
  plugin): one <key>.toml charter plus a <key>.jpg preview. Use when asked to
  create a custom Astro-Arc style or theme from an idea, to fix a theme that
  failed validation, or to explain a theme field.
---

# Astro-Arc themes

Astro-Arc paints a new wallpaper from the user's astrology every day. The day
decides what happens: its meaning, whether it is a **hard** day (under strain)
or a **soft** one, its light, who is in the scene. The theme decides how the
world looks and what it is made of. A model reads the theme's fields and the
day, writes one scene as an image prompt, and the theme's `suffix` is appended
before the image model paints it. Everything in a theme is therefore text a
model will act on: whatever a field names turns up in the pictures.

The pipeline already supplies, for every theme: the day's meaning and myth
choice, the hard/soft temper, the actor and what they are doing to the scene,
how worn things are, and a rule that the frame has somewhere for the eye to
go. A theme never restates these.

## The file

- `~/.config/astro-arc/styles/<key>.toml` (`$XDG_CONFIG_HOME/astro-arc/styles/`
  when that is set). The file name is the key: lowercase letters, digits and
  hyphens (`art-deco-posters.toml`). These keys are taken by shipped themes:
  cosmic, cyberpunk, engraving, ghibli, illuminated, symbolist, ukiyoe.
- `<key>.jpg` beside it, the preview, is required. Workflow step 5 makes it.
- Every field below is required unless it says optional. Strings are TOML
  basic strings in double quotes; escape inner double quotes as `\"`.
- A valid theme shows in the panel's Style dropdown as "<label> (custom)"; an
  invalid one as "(Failed Validation)", and it cannot generate.

## The rules

Each one has been measured on this project; break one and the pictures show it.

1. **A list is a menu.** The model copies whatever a field names. "The size of
   a barn" put barns into 21 of 33 days. Write a span and a principle with at
   most two examples, and never reuse an example noun in another field.
2. **Rules are counts and failure conditions, never moods.** "Make it feel
   open" is ignored; "at least half the frame is open sky or ground, and a
   sealed close-up is a failed response" is obeyed.
3. **The suffix is technique only**: line, shadow, colour, grain, light
   source, how figures are drawn. No objects, weather or places: anything the
   suffix names appears in every image.
4. **The light agrees with the palette.** A palette that implies a time of day
   (a dusk-only city, neon nights, one red sun) needs its own `[light]` lines
   saying how each band looks in this world, or the day's light and the
   palette fight.
5. **No protected names.** No studio, franchise or living artist anywhere in
   the file except the label ("Ghibli", "Pixar", "Star Wars", a working
   illustrator). Historical artists, movements and periods are fine
   (Hokusai, Art Deco, 1970s paperback covers). Describe what the frame looks
   like, not whose it is.
6. **Forcing a mood or a relationship costs the day its meaning.** A rule that
   always demands grief, a rescue, a villain or a cosy scene is allowed, but it
   overrides the chart: a calm day still gets grief, and the wallpaper stops
   telling the user about their day. The validator warns. Use one only when
   the user asks for it, and tell them this cost.
7. **Keep the top edge clear.** The desktop bar covers a thin strip at the
   top of the wallpaper. The technique must not crop figures at the top edge
   (no "figures cut off by the frame", no tall close-ups); heads and the main
   subject sit below it.

## Fields

### Top level

| Field | Count | What it controls; how to write it |
|---|---|---|
| `label` | 1 | The dropdown name. Display only, never sent to a model. Short. |
| `suffix` | 1 | Appended to every image prompt: the technique (rules 3, 5, 7). Describe the finished frame (line weight, shading, palette, texture, light source), not the process; artist names and process words are weakly followed. |
| `suffix_base` + `suffix_figures` | optional, instead of `suffix` | `suffix_figures` is appended to `suffix_base` only when a person is in frame: how figures are drawn. No separator is added, so start `suffix_figures` with a space or ", " that joins onto the end of `suffix_base`. |
| `space` | 6–8 | Depth recipes; one is drawn per day. Each one sentence, a different way this world shows depth: a place, not a mood ("across a harbour to a ruled horizon"). |

### `[world]`

Read by every model call. All required.

| Field | What it controls; how to write it |
|---|---|
| `era` | Period and technology. Say what the technology is like and how people relate to it, not a list of machines. |
| `materials` | What things are made of. The first ones listed dominate; lead with what you want most. |
| `physics` | How the world behaves: what may glow, float or change. State what is impossible as plainly as what is possible ("nothing glows of its own accord"). |
| `society` | Settlements, institutions, daily life. Include days that are not work, or every figure is at a chore. |
| `weather` | The range of weather and light as presence. Name the whole range and say to pick what the day calls for, or it defaults to fine weather. |
| `inhabitants` | Who and what lives here. Span kinds of being (people, creatures, spirits, machines), as often off work as on it. |
| `strangeness` | How the strange is treated. Say how people react (usually: they don't). |
| `numinous` | How the sacred appears. Give it a body; a world silent here never gets a sacred presence in frame. |
| `dread` | How the world goes dark on hard days. Physical and specific; the frame does not take sides. This is what makes hard days read as hard. |
| `favoured_actors` | Which kinds of actor this world is richest in, in prose. The kinds: man-beast, solitary, figures-in-relation, crowd, handwork, animal, swarm, automaton, presence, the-dead, none. Name the most worn figure of the genre and demote it. |
| `myth` | The traditions the day's meaning is found in. Name traditions, not stories. |
| `highlight` | What the eye finds first, as a principle, ending with "Today's kind of wrongness is given with this definition." Name no size comparison. |
| `highlight_modes` | 4–6 kinds of wrongness, one drawn per day. Each a different kind, not a different size. |
| `forbids` | Optional. Read out last as "Never in this world". Prefer saying what the world is. |

### `[brief]`

Rules added to the scene-writing brief, in file order.

| Key | What it controls; how to write it |
|---|---|
| `signature` | Something in frame that could only exist in this world. A count, ending "... is a failed response." Let it be the highlight or the actor, so it costs no extra objects. |
| `temper` | Carries the day's hard/soft temper to the highlight. Write exactly: `"TEMPER — the highlight, and anything numinous in the frame, is in this temper: {wonderTone}"` |
| any other key | Optional, a rule only this world needs (e.g. `neon`: when the scene is dark, the main light is neon tube). A count and a failure condition. |

A rule may use the day's values as `{placeholders}`; `{wonderTone}` is the
useful one. Others: activity, actorVerbs, anomalyRelation, condition,
exposureBand, highlightMode, intensityBand, light, mode, objectRelation,
polarity, prominence, registerCohesion, siteRelation, space, spatial,
tonalDirection, verbs. Any other `{name}` is an error; write literal braces
as `{{` and `}}`.

### `[light]`

Exactly `lo`, `mid` and `hi`: the light line for the day's exposure band
(night or no daylight / one clear direction / full even day). Each a count
and a failure condition, in this world's terms (rule 4). The example's three
lines are a good start where they hold.

### `[verbs]`

| Field | Count | What it controls |
|---|---|---|
| `process_hard`, `process_soft` | 20–30 each | Things happening with no one doing them; 3 drawn per day by polarity. |
| `actor_hard.<activity>`, `actor_soft.<activity>` | 20–30 in total per polarity | What the actor does. Activities: `work`, `rest`, `travel` required, plus `play` or `ritual` (or both). One activity is drawn per day, then 3 of its verbs. |

Verbs this world's physics allows, and specific to it: "silting up",
"jamming its points", "gilding" rather than generic motion and change
("drifting", "flooding", "hauling", "settling"), which every theme reaches
for and which then warns as shared with another theme's same array (fine
when it is native to both). No repeats within an array. Give rest, play,
travel and ritual as much life as work. A verb that takes
an object ends ready for one ("smoking a pipe at", "rowing across").

### `[registers]`

One line for each of these 20 materials (the day picks one or two), all
required, keys exactly as written:

botanical, bodily and anatomical, fungal and decaying, animal and creaturely,
geological, metallic and ore, glass and ceramic, architectural, domestic
interior, mechanical, industrial and infrastructural, agricultural and
cultivated, aquatic, atmospheric, glacial and frozen, fire and ember, textile
and fiber, ritual object, paper and inscription, instrument and measure.

Each line: what that material means in this world, as a span and a principle
with two examples (rule 1). The 20 lines must not all pull toward the same
setting.

## Example: the shipped engraving theme

`engraving.toml`, annotated. Comments (`#`) are allowed in a theme.

```toml
label = "Antique Engraving"
# Technique only: line, tone, finish. "star atlas illustration" names a
# genre of plate, not an object to paint.
suffix = "antique astronomical engraving, fine linework, star atlas illustration, sepia and ink tones, detailed etching"
# Eight different ways to show depth, each a place.
space = [
  "a landscape in section, the cutaway ground in front and the country running back behind",
  "across a harbour to a horizon ruled with latitude lines",
  "from an observatory platform up into a figured sky",
  "down a long hall of specimens and instruments",
  "a bird's-eye plan of a town tilting into a view",
  "along a voyage's coastline toward a cartouche-framed sea",
  "up a stair of endless arches",
  "through a demonstration's frame onto open country",
]

[world]
era = "The engraved universe of the natural philosophers, from the first star atlases to the encyclopaedias: a world measured, labelled and cut open to show how it works, where the heavens are drawn as figures over the stars and the earth is drawn in section."
# Materials first-listed dominate: line, then the common stuff.
materials = "Copper-plate line: everything is hatching, stipple and outline, and every material is known by its texture of lines. Brass instruments, stone, timber, sailcloth and paper are the common stuff, and the sky is a field of numbered stars."
# Says what is impossible ("Nothing glows") as plainly as what is possible.
physics = "The star chart is part of the world, not only its frame: constellation figures, graticule lines and labelled stars run around the edges of the image and into it, merging with the land, the sea and the things in them. Cutaways open hills and bodies to show what is inside. Nothing glows; light is the white of the paper between the lines."
# Names the days that are not work.
society = "Observatories, workshops, ships, gardens and lecture halls, where people measure, collect and make. Days are not all work: a crowd gathers for an eclipse, travellers stop at an inn, a family walks out to see a comet."
weather = "Weather is drawn both as diagram and as event: wind as a puffing head at a map's corner, a storm as massed hatching over a sea. Eclipses, comets and aurorae are weather here."
inhabitants = "Astronomers, instrument makers, voyagers, gardeners and the curious public; animals as specimens and as living things in the plate; and the constellation figures themselves, who step half out of the chart into the scene."
# How people react: they don't.
strangeness = "The cabinet of curiosities made true: a sea monster from a map's margin swimming in the real sea, a mountain cut open to show its fires. Nobody in the plate is surprised; it has been catalogued."
# The sacred has a body, drawn like everything else.
numinous = "The constellations are the numinous: heroes and beasts drawn in line over the stars, with bodies that can enter the world, like the Bear walking the horizon or the Ship riding a real sea. They are drawn exactly like everything else, in hatching, never as a glow."
# Physical and specific; the frame takes no side.
dread = "This world at its darkest is the plate of catastrophe: an eclipse over a plague town, a prison of endless stairs. It is recorded, labelled and precise, and the precision is what makes it cold."
# Names the genre's most worn figure and demotes it.
favoured_actors = "Crews and companies working one instrument or one voyage, specimens and creatures, and constellation figures in the world. A lone scholar at a desk is the most worn image here and should not be the default."
# Traditions, not stories.
myth = "The Greco-Roman myths behind the constellations, Perseus and the sea monster among them, and the emblem books of the Hermetic philosophers, where the world above mirrors the world below."
highlight = "The thing the eye finds first, drawn as a specimen or a demonstration: a creature, a constellation figure or a working of the world shown whole, labelled or cut open. Today's kind of wrongness is given with this definition. It is drawn in the same line as everything else."
# Five different kinds of wrongness; only one is about size.
highlight_modes = [
  "a constellation figure stepped down into the world",
  "a creature from a map's margin, here in life",
  "a thing shown in cutaway while it is still in use",
  "far too large for its kind, with a measuring line beside it",
  "a diagram's geometry built as a real structure",
]

[brief]
# A count ("at least one"), a span of four, and a failure condition.
signature = "SIGNATURE — the star chart is in the world: at least one named element of celestial cartography (a constellation figure, numbered stars, a graticule line, a cartouche) runs from the edge of the image into the scene and merges with it. A plate that could be any old etching is a failed response."
temper = "TEMPER — the highlight, and anything numinous in the frame, is in this temper: {wonderTone}"

# This world has no fixed time of day, so the general lines hold.
[light]
lo = "It is night, or the scene is somewhere daylight does not reach. At least two thirds of the frame is dark, and what is happening is only partly visible — lost in shadow, fog or depth rather than described. The only light is what the scene itself provides. A frame in which most of the image is well lit is a failed response."
mid = "The light comes from one clear direction, and lit and unlit areas are both substantial. A frame lit evenly from everywhere is a failed response."
hi = "It is full day — flat overcast, open daylight or hard noon sun — and the light is even across the frame. Everything is visible: nothing is hidden and nothing is dramatised by shadow. A frame that is mostly dark, lit by a low or setting sun, or lit mainly by the scene's own lamps and signs is a failed response."

[verbs]
process_hard = ["erupting", "collapsing inward", "quaking", "plunging", "blazing", "keeling over", "foundering", "caving in", "rending", "unravelling", "blackening", "overflowing its banks", "raining down", "withering", "careening", "breaching", "spewing smoke", "crumbling to rubble", "smashing", "sundering", "gushing", "tilting off its axis"]
process_soft = ["rotating", "revolving", "ticking", "swinging", "setting", "waxing", "waning", "branching", "spiralling", "flowing", "ebbing", "blossoming", "throwing a shadow", "balancing", "aligning", "crystallising", "mirroring", "germinating", "seeding", "floating on the tide", "unrolling", "converging"]
# 24 hard actor verbs across all five activities; rest, play and ritual are as alive as work.
actor_hard.work = ["winding a capstan", "hoisting", "sounding the depth", "chiselling", "quarrying", "harpooning", "felling", "dragging a net", "forging", "rowing hard against"]
actor_hard.rest = ["waiting out the eclipse beside", "huddling at", "lying low under"]
actor_hard.play = ["wrestling for sport", "fencing", "skating"]
actor_hard.ritual = ["carrying relics in procession", "lighting beacon fires on", "taking an oath before"]
actor_hard.travel = ["riding post toward", "sailing close-hauled past", "climbing the rigging of", "crossing on foot", "marching toward"]
actor_soft.work = ["measuring", "grinding a lens", "engraving a plate", "pressing flowers", "cataloguing", "potting", "weaving"]
actor_soft.rest = ["reading beside", "taking supper at", "resting on", "smoking a pipe at"]
actor_soft.play = ["playing a viol at", "stargazing from", "feeding swans at", "dancing on"]
actor_soft.ritual = ["laying flowers at", "keeping a feast at", "blessing"]
actor_soft.travel = ["promenading along", "riding out to", "rowing across", "arriving by coach at", "walking out to"]

# Each line: the material in this world, a principle, two examples.
[registers]
"botanical" = "Plants as the herbalist and the gardener drew them: a specimen shown root, leaf and seed at once, a formal garden laid out in beds. Every plant is identifiable and could be labelled."
"bodily and anatomical" = "The body as the anatomist's plate: a figure with its muscles lifted like a cloak, a skeleton leaning on a spade. Exact and calm, a demonstration, never gore."
"fungal and decaying" = "Rot recorded with the same care as growth: a toadstool ring drawn as a specimen, a timber eaten into galleries by worms. Decay here is a subject of study."
"animal and creaturely" = "Animals as the naturalist's specimens and the traveller's wonders: a rhinoceros drawn from a description, a whale on a beach with a crowd around it. Exotic and ordinary animals get the same line."
"geological" = "The earth in section: a mountain cut open to show its veins and fires, strata drawn like the pages of a book. Stone is something to measure and to look inside."
"metallic and ore" = "Metal as mine and instrument: a mine's shafts and windlasses in cutaway, brass rings of an armillary sphere. Metal is worked, measured and named."
"glass and ceramic" = "Glass as the instrument of seeing: a telescope's lenses, the alembics of a laboratory. Glass makes the distant near and the small large."
"architectural" = "Buildings drawn as elevations, plans and ruins: an observatory dome opened to the sky, a colonnade in perspective. Architecture shows its geometry."
"domestic interior" = "A room as a study or a cabinet: shelves of specimens, a globe by the window. It always opens onto the world through a window or a door."
"mechanical" = "Machines shown working and in exploded view: a mill's gears, a clock's escapement. Every part is drawn as if it could be built from the plate."
"industrial and infrastructural" = "Works drawn as the encyclopaedia drew trades: a glassworks with its furnaces in section, an aqueduct striding across a valley. Each shows how it is done."
"agricultural and cultivated" = "Husbandry as instruction: a field ploughed in demonstration, an orchard's grafting shown step by step. The subject is the method and the yield."
"aquatic" = "Water as the cartographer and the engineer drew it: a coastline with its soundings, a fountain's pipes in section. Seas carry ships and monsters alike."
"atmospheric" = "Air as diagram and as event: a comet's tail across a town, a storm's winds drawn as lines. The sky is charted as carefully as the land."
"glacial and frozen" = "Ice as the polar voyagers drew it: a ship locked in a frozen sea, snowflakes drawn as six-sided specimens."
"fire and ember" = "Fire as furnace and as catastrophe: a volcano's eruption in section, an alchemist's athanor. Fire is a force to be shown at work."
"textile and fiber" = "Cloth as sail and as trade: a loom in exploded view, a ship's canvas full of wind. Fibre is drawn by its weave."
"ritual object" = "The emblems and instruments of the Hermetic philosophers: a sigil engraved on a plate, an orrery used as an altar. Ritual objects are drawn like instruments."
"paper and inscription" = "Paper as the plate's own world: banners, cartouches and labels that carry the names of things, a book open on a lectern. Lettering is ornamental and partly legible, never a sentence."
"instrument and measure" = "Measure is this world's heart: quadrants, astrolabes, rulers and graticules. To measure is to make the world readable."
```

## Workflow

`astro-arc-style` is the plugin's command:
`~/.config/omarchy/plugins/astro-arc/backend/bin/astro-arc-style` (the brief
that launched you may give its exact path). `preview`, `meaning` and `render`
spend the user's API credit: each prints its cost and spends nothing without
`--yes`. Show the user that cost and get their yes before passing `--yes`; one
yes may cover several runs of the same command if the user says so.

1. **Decide the world.** From the user's idea, settle in a few lines: the
   technique (what a finished frame looks like), the world's era and
   materials, what is strange in it, how it goes dark, and whether its palette
   implies a time of day (rule 4). Done when each `[world]` field has a
   one-line intent.
2. **Write the file** at `~/.config/astro-arc/styles/<key>.toml`, every field,
   following the rules and the example's shape (not its nouns). Done when
   the file exists and every rule above has been checked against it.
3. **Validate.** `astro-arc-style validate <file>`. Fix every error and every
   warning that is not deliberate, and repeat. Done when the only error left
   is the missing preview.
4. **Preview the prompts.** `astro-arc-style preview <file> --yes` writes six
   scene prompts, three for a real hard day and three for a soft one. Read
   them all. Done when: every prompt is recognisably this world; no noun
   from your own fields turns up in most prompts (a menu leak, rule 1); the
   hard ones read as strain and the soft ones do not. Otherwise fix the
   fields that caused it and repeat from step 3.
5. **Check the meaning.** `astro-arc-style meaning <file> --yes` replays the
   last 8 real days and has a blind judge score them. Done when it prints
   `MEANING: PASS` (at least 6/8 hard/soft read right, at most 1
   near-duplicate pair, at most 2/8 in the theme's favourite scene). On FAIL
   it lists the failing prompts with reasons; fix by the table below and
   repeat from step 3.
6. **Make the preview.** `astro-arc-style render <file>` shows the render
   cost at the user's own image settings; with their yes,
   `astro-arc-style render <file> --yes`. One render becomes `<key>.jpg`.
   With `--n 3`, show the user the files and set their pick with
   `astro-arc-style choose <file> <image>`. The user may instead give an
   image they have: `choose` it.
7. **Finish.** `astro-arc-style validate <file>` shows 0 errors. Tell the
   user the theme is in the Style dropdown as "<label> (custom)", what it
   cost, and any warning you left on purpose.

### When `meaning` fails

| Failure | Usual cause | Fix |
|---|---|---|
| Hard days read soft | `dread` vague; `process_hard` and `actor_hard` too gentle; a brief rule forcing a pleasant mood; light lines that light everything | Make `dread` physical, put real strain in the hard verbs, remove the forced mood (rule 6), check `[light] lo` |
| Soft days read hard | The palette or suffix is gloomy by itself; `physics` or `strangeness` menacing | Move darkness out of the suffix into `dread`; let soft verbs be at ease |
| Near-duplicates | A field acting as a menu: one example noun or setting in a register line, `space`, `signature` or `highlight` | Replace it with a span and a principle (rule 1) |
| Favourite scene | `signature` or `highlight` describes one composition; `favoured_actors` names one figure | Widen the span: several kinds, none described as a scene |
