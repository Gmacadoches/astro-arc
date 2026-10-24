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
