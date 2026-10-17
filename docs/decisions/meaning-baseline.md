# `meaning` baseline: the seven shipped themes

`astro-arc-style meaning` (decision 7) run on every shipped theme, so its pass
bar (at least 6/8 hard/soft read right, at most 1 near-duplicate pair, at most
2/8 in the theme's favourite scene) is checked against themes known to be
good. The bar is not tuned to these results; a shipped theme that fails is
reported here as it stands.

Run 2026-10-17 on the create-your-own-style branch. The same 8 real days for
every theme (2026-10-10 to 2026-10-17: 4 hard, 4 soft, each from its stored
texture), replayed prompts-only with each theme's own draws; Stage 1.5 and 2
and the judge on gpt-5.4, the judge seeing the 8 Stage 2 prompts shuffled with
no dates or readings. Records (every prompt, the judge's reply) are in
`~/.local/state/omarchy/astro-arc/verification/style-meaning/`.

| Theme | Verdict | Hard/soft right | Near-dup pairs | Favourite scene | Cost | Record |
|---|---|---|---|---|---|---|
| cosmic | PASS | 8/8 | 0 | 0/8 | $0.26 | `cosmic-20261017-091607.json` |
| cyberpunk | **FAIL** | 7/8 | 0 | 4/8 | $0.28 | `cyberpunk-20261017-091612.json` |
| engraving | PASS | 7/8 | 0 | 0/8 | $0.25 | `engraving-20261017-091423.json` |
| ghibli | PASS | 7/8 | 0 | 2/8 | $0.26 | `ghibli-20261017-091604.json` |
| illuminated | not run | | | | | |
| symbolist | not run | | | | | |
| ukiyoe | not run | | | | | |

**Not run: illuminated, symbolist, ukiyoe.** The OpenAI account ran out of
credit during their replays (429 `credit_balance_exhausted`), so nothing was
recorded for them. Run them once credit is added and fill in their rows:

```sh
for k in illuminated symbolist ukiyoe; do
  backend/bin/astro-arc-style meaning backend/pipeline/styles/$k.toml --yes
done
```

## Failures

**cyberpunk**: favourite scene 4/8 (bar: at most 2). The judge's favourite scene: "A shrine-like machine saint in a transit maintenance space is being quietly tended with small offerings under bent neon." Prompts 1, 3, 6, 8 are that picture: a machine or junction-box saint tended with offerings and prayer stickers, three of them on a stairwell "dropping floor after floor". The stairwell is the `space` recipe "down a stairwell falling away floor after floor", the sticker is the `actor_soft.ritual` verb "pasting a prayer sticker on", and the shrine is the `myth` and `numinous` (gods moved into hardware) arriving as the same scene every soft day. A charter that lets one composition win, not a fault in the bar. Not changed here.

## Notes

- Hard/soft is the strongest signal: every theme run reads 7 or 8 of 8 right.
  The misses: engraving, a soft day read hard (the day's material was rot in a
  ship's stores); cyberpunk, a hard day read soft (shelter and sleep under the
  dark); ghibli, a soft day read hard (the highlight's wrongness read as
  menace).
- The registers walk in cold-start order (botanical, bodily, ...) because the
  replay's history starts empty; the same 8 registers for every theme.
