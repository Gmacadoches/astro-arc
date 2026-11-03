# Astro-Arc

Astro-Arc turns a natal chart and the period's dominant transit into a daily desktop theme: an LLM pipeline writes an image prompt in a chosen style's world, the image is rendered, and its palette becomes the theme.

## Language

**Composition**:
Everything between a Stage 1 reading and the final image prompt: the draws, the register pair, Stage 1.5's meaning and material, Stage 2 and the melodrama check. Production and every harness run the same composition.
_Avoid_: build, pipeline run, generation (a generation also renders and applies the theme)

**History entry**:
The record one composition leaves behind, from which later runs derive their recency. It is committed only once the image exists, so a refused render doesn't move the rotation on.
_Avoid_: history row, run record

**Recency**:
What a run avoids or rotates because recent runs used it: draws, material, registers, concepts, and in production the recent constellations and actor kinds. It comes only from the history a composition is given.
_Avoid_: variety, freshness, cooldown

**Pin**:
A draw, register pair or meaning supplied from outside a composition in place of the one it would choose, so a harness can hold one variable still.
_Avoid_: override, fixture, forced value

**Pipeline state**:
What the pipeline remembers between runs: the saved history, the period's cached reading and the period's cached meaning. Production has one; a test or harness either brings its own or runs with none.
_Avoid_: cache (it is one part of the state), state dir

**Cache key**:
The inputs a cached reading or meaning was produced from: the birth data and prompt version, and for a meaning the style and its charter too. A cached result is reused only for a run whose inputs match it.
_Avoid_: cache validity, natal hash (one input among several)

**Catalog**:
The styles, registers, actor kinds and clichés as read at the start of a run. Every choice in a composition is made from one catalog, and whether a style is usable is a question about the whole catalog, since its rules name the registers and its advice compares it with the other styles.
_Avoid_: style list, config, loaders

**Style**:
One world an image can be painted in: a `<key>.toml` charter and a `<key>.jpg` preview in the user's styles folder. Every style is the user's own file, to tweak, hide or delete, whether it came with Astro-Arc or the user made it.
_Avoid_: theme (in the panel a theme is the colours applied to the desktop; the code still says theme for a style file), custom style, built-in style, core style

**Seed**:
A style file that ships with Astro-Arc, in `backend/pipeline/styles/`. Seeding copies each seed into the user's styles folder once; a run never reads a seed itself. A later release's version of a seed replaces the user's copy only while that copy is unchanged.
_Avoid_: shipped style, default style

**Starter style**:
A style that came from a seed, as opposed to one the user made. It gets Astro-Arc's updates until the user changes it, and can be reverted to the seed or restored after deleting. The ten named in `starter-styles.toml` start in view; the rest start hidden.
_Avoid_: core style, curated style

**Hidden**:
A style left out of the Style picker but still installed, still listed under Styles → Show hidden, and still usable once shown. The selected style is never hidden.
_Avoid_: disabled, off, archived

**Favourite**:
A starred style. Starred styles are listed first, in the picker and in the Styles view. Starring is a setting, never part of the style file.
_Avoid_: pinned (a pin is a harness term), ranked

**Setting**:
One thing the user chooses, such as the schedule, the style or the models. Each has one default and one set of values it may hold, the same for the panel and the pipeline. A value outside that set is read as the default.
_Avoid_: config option, preference, flag (a flag is how a setting is changed)
