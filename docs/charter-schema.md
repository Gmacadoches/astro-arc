# Style charter schema

A style in `backend/pipeline/styles.toml` is a **technique** (how the image is
painted) and a **charter** (what world it shows). This is every field a charter
can have, what reads it, and the one rule that matters most when writing it.
It is the contract a future "create your own style" feature generates against.

**A charter is required.** There are no neutral fallbacks: every table and
field below is required unless it is listed under Optional.
`llm_pipeline.validate_charter` is the single definition of a complete
charter. A style with any error cannot be used for a run, and each error names
the style, the field and the rule. The validator also prints warnings for
choices the schema allows but advises against (see Warnings).

Two rules hold for every field, because this project has measured both:

- **A list is a menu.** Whatever a field names, the model copies. "The size of
  a barn" put barns into 21 of 33 days; naming a neon fish put fish on most
  signs. Write a span and a principle, with at most two examples, and never
  reuse an example noun in another field.
- **Rules are obeyed as counts and failure conditions, never as moods.**
  "Make it feel open" is ignored; "at least half the frame is open, and a
  sealed close-up is a failed response" is not.

## `[<style>]`

| Field | Controls | Write it well |
|---|---|---|
| `label` | the name in the Style dropdown | Short and recognisable. |
| `suffix`, or `suffix_base` + `suffix_figures` | appended to every image prompt: the technique. `suffix_figures` is added only when a person is in frame | Describe what the finished frame **looks like** (line, shadow, light source, grain), not how it was made; artist names and process words are weakly followed. **Technique only:** name no objects, weather or places, because anything named in the suffix appears in every image. A thin strip at the top of the wallpaper is covered by the desktop bar, so the technique must not crop figures at the top edge. |
| `space` | 6–8 depth recipes; one is drawn per run, sent with the WALLPAPER rule | Each recipe a different way this world shows depth, one sentence, a place not a mood. |

## `[<style>.world]`

Sent to Stage 1.5 (both calls) and Stage 2, in this order. All 13 fields are
required.

| Field | Controls | Write it well |
|---|---|---|
| `era` | the period and its technology | Say what the technology is like and how people relate to it, not a list of machines. |
| `materials` | what things are made of | Lead with the materials you want most; whatever is listed first dominates. |
| `physics` | how the world behaves; what may glow, float or change | State what is impossible here as plainly as what is possible. "Nothing glows of its own accord" is a rule the model follows. |
| `society` | settlements, institutions, daily life | Include the days that are not work, or every actor will be at a chore. |
| `weather` | the range of weather and light as presence | Name the whole range and say to pick what the day calls for, or it defaults to fine weather. |
| `inhabitants` | who and what lives here | Span kinds of being (people, creatures, spirits, machines), and say they are as often off work as on it. |
| `strangeness` | how the strange is treated | Say how people react to it (usually: they don't). |
| `numinous` | how the sacred appears; makes the `presence` actor kind eligible | Give it a body. A world that says nothing here cannot have a `presence`. |
| `dread` | how the world goes dark on hard days | Physical and specific; say the frame does not take sides. |
| `favoured_actors` | which actor kinds the world is richest in | Name the most worn figure of the genre and demote it. |
| `myth` | the traditions the day's meaning is found in (Stage 1.5's meaning call) | Name traditions, not stories; the meaning call picks the story. |
| `highlight` | what the thing the eye finds first is in this world | A principle, plus "today's kind of wrongness is given with this definition" when there are modes. Name no size comparison. |
| `highlight_modes` | 4–6 kinds of wrongness; one drawn per run | Each a different kind, not a different size. |

## `[<style>.brief]`

Rules appended to Stage 2's composition brief, in file order. They may use the
dial's values as `{placeholders}`, most often `{wonderTone}`.

| Key | Controls | Write it well |
|---|---|---|
| `signature` (required) | something in frame that could only exist in this world | End with "…is a failed response". Let it be the highlight or the actor, so it costs no words. |
| `temper` (required) | carries the day's hard/soft temper to the highlight | `TEMPER — … in this temper: {wonderTone}`. |
| any other key | a style-specific rule (cyberpunk's `neon`) | A count and a failure condition. |

A `{placeholder}` must be a value the dial supplies; the validator lists them
when one is not. Forcing a mood or a relationship in a rule (grief, rescue, a
villain) is allowed as the creator's choice, but it warns: the chart, not the
charter, is meant to decide how a day feels.

## `[<style>.verbs]`

| Field | Controls | Write it well |
|---|---|---|
| `process_hard`, `process_soft` | 20–30 each; 3 drawn per run from the day's polarity. Things happening with no one doing them | Verbs this world's physics allows; share none with another style's same array unless native to both. |
| `actor_hard.<activity>`, `actor_soft.<activity>` | activities `work`, `rest`, `play`, `ritual`, `travel` (work, rest and travel required, plus play or ritual); 20–30 in total per polarity. One activity is drawn per run, then 3 of its verbs | Give the non-work activities as much life as the work. |

## `[<style>.registers]`

One line per register in `registers.toml` (20): what that substance means in
this world. Read by both Stage 1.5 calls and Stage 2. May end with
"Its highlight:" to say what form the highlight takes in that material.
**Write it well:** a span and a principle with two examples; the line must not
pull every register toward the same setting.

## `[<style>.light]`

Exactly `lo`, `mid` and `hi`: the light line Stage 2 gets for each exposure
band. There are no global light lines. **Write it well:** a count and a
failure condition, like every other rule. A palette that implies a time of day
(a dusk-only city, a world under one star) needs light lines that agree with
it, or the day's exposure band and the palette argue. The pre-2026-10-16
global lines (night / one clear direction / full day) are a good starting
point where they hold.

## Optional

| Table | Controls | Write it well |
|---|---|---|
| `forbids` in `[<style>.world]` | read out last as "Never in this world" | Prefer saying what the world is. No shipped style uses it. |
| `[<style>.conditions]` | replaces the condition list (newly made … ruined) | Only for a world that is always pristine or always ruined. |

## Warnings

These never block a run:

- a verb shared with another style's same array (fine if it is native to both)
- a `[brief]` rule with no "failed response"
- a `[brief]` rule that requires a mood or relationship

## Checking a charter

```sh
python3 -c 'import sys; sys.path.insert(0, "backend/bin"); import llm_pipeline as lp; print(lp.validate_charter("<style>"))'
sweep.py --out mine-before --style <style> --pairs --rotate-cells --registers <8, across all five families>
# edit the charter
sweep.py --out mine-after  ... (same)
term_report.py mine-before mine-after
overlap_report.py <prefix> <style> <another style>
```

`term_report.py` reports the repeated verbs, the attractor words and what the
actors are doing, against the overhaul's pass conditions. Rotate dial cells; a
pinned cell repeats its own sentences and reads as a style problem.
