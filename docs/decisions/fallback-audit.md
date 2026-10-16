# Fallback audit (Oct 16, 2026)

This audit is for decisions 2 and 4 in `2026-10-styles.md`. It shows which
fallbacks for unchartered styles can still be reached. It changes and deletes
nothing.

## How it was measured

- **Static.** I grepped `backend/bin/*.py`, `backend/bin/astro-arc-*`,
  `Model.js` and `*.qml` for each symbol. Model.js and the QML have no hits;
  everything is in Python.
- **Runtime.** `backend/bin/fallback_audit.py --out baseline` runs the real
  `llm_pipeline.build()` for each of the 7 shipped styles, across 28 dial cells
  (polarity hard/soft/none × intensity lo/mid/hi × exposure lo/mid/hi, plus no
  texture) and 20 registers × 2 secondaries (none, or the next register). That
  comes to **7,840 Stage 2 prompts**.
  - Every model call goes to a stub, and network access raises an error, so
    nothing was billed.
  - Pipeline state is redirected to a temp directory, so the real history and
    caches are neither read nor written.
  - A synthetic style with no charter runs as a positive control. It confirms
    that each detector can fire. It is not part of the hash.
- **Baseline.** The output is in
  `~/.local/state/omarchy/astro-arc/verification/baseline/`:
  `prompts.jsonl`, `prompts.sha256` and `fallbacks.json`. The sha256 is
  `c73cf142f193dacdf27970f59250096c5d6eae47b239261479833221f0efa66a`.
  - A second run produced the same hash.
  - The run takes 5 s because `load_styles()` and `_load_actors()` are cached
    for the run. Without the cache, `actors.toml` is parsed about 24 times per
    build and a single style takes over 2 minutes. The cache does not change
    the hash, and `--no-cache` turns it off.

## Results

All line numbers are in `llm_pipeline.py` unless a file is named.

| Symbol | Every caller | Fires at runtime? | Proposed verdict |
|---|---|---|---|
| `DEFAULT_STAGE2_BRIEF` | `load_style_brief` :644 (style has no `[brief]`); `stage2_image_prompt` :2467 (`style_brief is None`) | **No.** All 7 styles have `[brief]`, and every in-repo caller passes `style_brief`. The only exception is `sweep.py:366`, the `TypeError` path for an older pipeline signature. Control: fires. | Dead |
| `DEFAULT_SIGNATURE`, `_default_signature` | `load_style_brief` :644 (unchartered), :652 (no key from `SIGNATURE_KEYS` in `[brief]`) | **No.** Ghibli has `impossible`, and the other 6 have `signature`. Control: fires. | Dead |
| `SIGNATURE_KEYS` | `load_style_brief` :651 | Checked on every run but never triggers anything | Live only through a fallback. It exists to decide whether to append the default signature. If the validator requires a signature rule, the check moves there. |
| `DEFAULT_HIGHLIGHT` | `load_style_highlight` :595; `stage2_image_prompt` :2412 (`highlight or DEFAULT_HIGHLIGHT`) | **No.** Every charter has `highlight`, and every caller passes it. Control: fires. | Dead |
| `SPACE_REPLACES_BRIEF` | `load_style_brief` :649 | **Yes:** ghibli and cyberpunk, 1,120 runs each, dropping `openness` and `depth` respectively | Live for another reason. It is not a fallback: it drops a fixed depth rule that `[space]` supersedes. Once `[space]` is required, a validator error for an `openness`/`depth` key could replace it. |
| `_VERBS_HARD`, `_VERBS_SOFT` | `dial_from_texture` :1418 (seed); `_global_verbs` :1513–1514; `term_report.py:183` (reads it by name) | Seeded in all 7,560 textured runs, then overwritten by `draw_style_choices` every time. They never reach Stage 2. | Live only through a fallback. Removing them means removing the seed and the `term_report.py` lookup. |
| `_ACTOR_VERBS_HARD`, `_ACTOR_VERBS_SOFT` | `dial_from_texture` :1420 (seed); `NO_TEXTURE_DIAL` :1371; `_global_verbs` :1515–1516; `term_report.py:183` | Same as above. They never reach Stage 2. | Live only through a fallback |
| `_global_verbs` | `draw_style_choices` :1613 (`load_style_verbs(...) or _global_verbs()`) | **No.** All 7 styles have `[verbs]`. Control: fires. | Dead |
| `NO_TEXTURE_DIAL` | `dial_from_texture` :1390; `stage15_meaning` :1839, `stage15_material` :1999 and `stage2_image_prompt` :2369 (each when `dial is None`); `replay_days.py:65` and `replay_amplify.py:78` (base dict) | **Only for a reading with no texture:** 40 runs per style. In that cell Stage 2 gets **no light line at all**, and polarity silently becomes soft. Production never produces such a reading: `astro_engine.compute_texture` has no path that returns None, and `astro-arc-generate` always runs `astro_engine`. The three `dial is None` branches never fired. | Live only through a fallback (a texture-less reading). Replace it with a loud error in `build()`. The two replay harnesses should rebuild their dial from the stored texture. |
| `_LIGHT_BY_EXPOSURE` | `dial_from_texture` :1425 | **Yes, in production.** The global light line reaches Stage 2 for engraving, ghibli, illuminated, symbolist and ukiyoe (1,080 runs each). Only cosmic and cyberpunk have `[light]`. | Live only through a fallback (missing `[light]`), but it is the live path for 5 of 7 styles. Decision 4 removes it, which first needs 5 new `[light]` tables. |
| `CONDITIONS` | `draw_style_choices` :1641 (`load_style_conditions(...) or list(CONDITIONS)`) | **Yes.** All 7 styles, every run, because no style has `conditions`. | Live for another reason. It is the pipeline's wear scale, which decision 4 keeps in the pipeline. The fallback is the optional per-style `conditions` override, which nothing uses. Proposal: keep `CONDITIONS` and remove the override. |
| `load_style_info` | `build` :2531; `sweep.py:296`; `replay_days.py:112`; `build_style_gallery.py:100` | Its fallbacks did not fire: no unknown key, and no style without a charter. Control: "no charter, no `guidance`", which drops the style line. | Live for another reason: it supplies the label and the world. Its two internal fallbacks (unknown key → symbolist, `world or guidance`) are dead. Decision 3 changes what the label does. |
| `load_style_world` | `build` :2526; `load_style_info` :701; `sweep.py:232, 244, 327`; `replay_days.py:127`; `replay_amplify.py:120`; `build_style_gallery.py:109` | Never returned `""` for a shipped style. Control: fires. | Live for another reason: it reads the charter. Its `""` return for an unchartered style becomes a loud error. |
| `guidance` (TOML field) | `load_style_info` :701 (`preset.get("guidance")`) | **No.** No shipped style has the field. The other `guidance` hits are register lines (`sweep.py:339–350`) or a local variable (`build_style_gallery.py:100–116`). | Dead |
| `or`/`is None` in `stage2_image_prompt` | `dial is None` :2368; `highlight or DEFAULT_HIGHLIGHT` :2412; `prominence or _PROMINENCE_BY_INTENSITY['mid']` :2413; `style_brief is None` :2467 | **No.** Every caller passes all four, and every dial has `prominence`. | Dead (only the old-signature path in `sweep.py:366` would reach them) |
| `or` in `draw_style_choices` | `polarity or "soft"` :1611; `_global_verbs` :1613; `CONDITIONS` :1641 | `polarity or "soft"` fires only in the no-texture cell. The other two are covered above. | Live only through a fallback, since it comes with `NO_TEXTURE_DIAL` |
| `or` in `dial_from_texture` | `if not texture` :1389; `polarity "none" → soft` :1396; `intensityBand or "lo"`, `exposureBand or "mid"`, `multiplicity or 0` :1398–1400 | `"none" → soft` fires in 360 runs per style. The missing-band defaults never fired: `astro_engine` always sets them. | `"none" → soft`: live for another reason. It is a defined cell (a day with no aspect in orb), not a fallback. Band defaults: dead. |

### Also found

These were not on the list, but they are the same kind of thing.

- **`load_style_suffix`** falls back to symbolist for an unknown key
  (:453), with the same fallback as `load_style_info`. It was not reached.
  Verdict: dead.
- **`load_register_line`** falls back to the generic `[guidance]` line in
  `registers.toml` (:1974). It was not reached by a shipped style, since all 7
  styles have all 20 register lines. Verdict: live only through a fallback.
  Decision 2 implies requiring a full `[registers]` table.
- **Actor repair** in `stage2_image_prompt` (:2378–2382) and
  `stage15_meaning` (:1942–1949): when Stage 1.5 returns no `actorKind` or an
  excluded one, the actor becomes `solitary`. The stub always returns
  `solitary`, so the audit never exercised this. It repairs a model response
  rather than covering for a style without a charter, so it is out of scope
  for decision 2.

## Where things stand

- **Not reached by any shipped style:** `DEFAULT_STAGE2_BRIEF`,
  `DEFAULT_SIGNATURE`/`_default_signature`, `DEFAULT_HIGHLIGHT`,
  `_global_verbs`, the `guidance` field, and the `or`/`is None` branches in
  Stage 2. These can be deleted after the full usage test that decision 2
  requires.
- **Still live in production:**
  - `_LIGHT_BY_EXPOSURE`, for 5 styles. They need `[light]` tables before it
    can go.
  - `CONDITIONS`, for all 7 styles. My proposal is to keep it as pipeline
    machinery and remove the per-style override.
- **Needs your approval** (live for another reason):
  - `SPACE_REPLACES_BRIEF`
  - `CONDITIONS`
  - `"none" → soft`
  - `load_style_info` and `load_style_world` as functions, without their
    fallbacks

## Approved verdicts (Oct 16, 2026)

Garrett approved the verdicts above with these changes. They are the input to
Prompt 2.

1. **Delete as dead:** `DEFAULT_STAGE2_BRIEF`,
   `DEFAULT_SIGNATURE`/`_default_signature`, `DEFAULT_HIGHLIGHT`,
   `_global_verbs`, the `guidance` field, and the `or`/`is None` defaults in
   Stage 2.
2. **`SIGNATURE_KEYS`** moves into the validator. After ghibli's `impossible`
   is renamed, only `signature` is accepted.
3. **`_VERBS_*` / `_ACTOR_VERBS_*`:** stop seeding them in
   `dial_from_texture` and delete the lists. `term_report.py` reads verbs from
   the styles instead.
4. **`SPACE_REPLACES_BRIEF`:** delete the `openness` brief key from ghibli and
   the `depth` key from cyberpunk, then delete the skip logic. Prompts must not
   change.
5. **`CONDITIONS`:** keep the global wear scale and remove the unused
   per-style `conditions` override.
6. **`load_style_info`:** fold it into `load_style_world`. The label is read
   for display only.
7. **`load_style_world`:** an empty result is an error. A full `[registers]`
   table is required, so delete the `load_register_line` fallback and the
   empty `[guidance]` table in `registers.toml`.
8. **Unknown style key:** do not just delete the fallback. Resolve the key once
   when the config is read: fall back to the default style **and** show a
   visible warning in the panel. Remove the per-loader unknown-key fallbacks
   (`load_style_suffix` and the others).
9. **`NO_TEXTURE_DIAL`:** production raises an error on a reading with no
   texture. The replay scripts build an explicit dial for a named cell. The
   missing intensity and exposure band defaults next to it also become errors,
   provided production always supplies the bands.
10. **Polarity `"none"` → soft** stays. Add a comment saying it is a defined
    cell.
11. `docs/decisions/2026-10-styles.md` is on this branch (commit `20489bb`).
12. The `load_styles()`/`_load_actors()` cache stays in `fallback_audit.py`
    only.

**Check for Prompt 2.** Rerun `fallback_audit.py` and diff it against
`baseline` (sha256 `c73cf142…a66a`). Item 4 must produce identical prompts.
Any other difference must come from an item above.
