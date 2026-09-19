#!/usr/bin/env python3
"""Builds the page for one Astro-Arc generation — the reading, the prompt, the
image and the tuning metadata that produced it — and files it in the archive.

Every generation gets its own folder in the archive (see paths.py):

    ~/.local/share/astro-arc/generations/<id>/
        index.html     this page
        theme/         the generation's complete Omarchy theme: colors.toml,
                       icons.theme, backgrounds/background.png (the wallpaper,
                       which the page shows by relative link), and whatever
                       else the theme generator produced
        thumb.jpg      the gallery's thumbnail

and is registered at the top of generations.json, which the panel's "Themes
Generated" list reads. The gallery (archive_gallery.py) is rebuilt afterwards,
so ~/.local/share/astro-arc/index.html always lists everything.

Pages written before 2026-09-19 carried the image inline as base64, about 5 MB
each; they still work, and astro-arc-migrate moved them here unchanged.

Called by astro-arc-generate after every real run.
"""

import argparse
import datetime
import json
import shutil
import sys
import tomllib
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import archive_gallery  # noqa: E402
from image_fit import ImageError, thumbnail_jpeg  # noqa: E402
from paths import GENERATIONS_DIR, GENERATIONS_INDEX  # noqa: E402

# The wallpaper's place inside a generation folder, relative to its page.
IMAGE_REL = "theme/backgrounds/background.png"
THUMB_REL = "thumb.jpg"
THUMB_PX = 640
STYLES_FILE = Path(__file__).resolve().parents[1] / "pipeline" / "styles.toml"

# Same fixed palette this page always used, kept as the fallback for a
# review built before --theme-dir existed (no snapshot to read a real
# palette from).
FALLBACK_THEME_VARS = {
    "bg": "#f6f4ef", "surface": "#ffffff", "surface2": "#efece2",
    "ink": "#1c1e2a", "inkDim": "#5d5f72", "accent": "#9c7a2e",
    "border": "#ded9c8", "good": "#3f7a5c",
}


def _dir_size(path):
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())


def _theme_css_vars(theme_dir):
    """Maps this generation's actual colors.toml onto the page's own CSS
    custom properties, so the review page visually wears the theme it
    produced — implicit in its background/text/borders/accent, not a
    swatch list — instead of a fixed palette that never changed no matter
    what the generation actually looked like."""
    if theme_dir:
        colors_path = Path(theme_dir) / "colors.toml"
        if colors_path.is_file():
            try:
                with colors_path.open("rb") as f:
                    c = tomllib.load(f)
                bg = c.get("background", "#14161f")
                return {
                    "bg": bg,
                    "surface": c.get("lighter_background", bg),
                    "surface2": c.get("dark_background", bg),
                    "ink": c.get("foreground", "#e8e6df"),
                    "inkDim": c.get("muted", c.get("dark_foreground", "#9a9bb0")),
                    "accent": c.get("accent", "#c9a24b"),
                    "border": c.get("selection", c.get("dark_background", bg)),
                    "good": c.get("green", "#7fc9a3"),
                }
            except (tomllib.TOMLDecodeError, OSError):
                pass
    return dict(FALLBACK_THEME_VARS)


def _style_label(style_key):
    """Resolves a style key (e.g. "ghibli") to its pipeline/styles.toml
    label (e.g. "Studio Ghibli") for display — falls back to the raw key
    if styles.toml can't be read or doesn't have it, rather than hiding
    the art style entirely."""
    if not style_key:
        return None
    try:
        with STYLES_FILE.open("rb") as f:
            styles = tomllib.load(f)
        preset = styles.get(style_key)
        if preset:
            return preset.get("label", style_key)
    except (tomllib.TOMLDecodeError, OSError):
        pass
    return style_key


PAGE_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<title>Astro-Arc — {escaped_label}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,600;1,9..144,500&family=Work+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');
  /* This generation's actual colors.toml, not a fixed palette — the page
     wears the theme it produced regardless of the viewer's own light/dark
     preference, since that's what's actually being shown here. */
  :root {{
    --bg: {theme_bg}; --surface: {theme_surface}; --surface-2: {theme_surface2};
    --ink: {theme_ink}; --ink-dim: {theme_ink_dim}; --accent: {theme_accent};
    --accent-soft: color-mix(in srgb, {theme_accent} 22%, {theme_bg});
    --border: {theme_border}; --good: {theme_good};
  }}
  * {{ box-sizing: border-box; }}
  body {{ background: var(--bg); color: var(--ink); font-family: 'Work Sans', system-ui, sans-serif; line-height: 1.55; padding: clamp(20px, 4vw, 56px); margin: 0; }}
  main {{ max-width: 1080px; margin: 0 auto; }}
  h1, h2, h3 {{ font-family: 'Fraunces', Georgia, serif; text-wrap: balance; margin: 0; }}
  h1 {{ font-size: clamp(24px, 3vw, 34px); font-weight: 600; }}
  h2 {{ font-size: 15px; font-weight: 600; }}
  .eyebrow {{ font-family: 'IBM Plex Mono', monospace; font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--accent); }}
  header {{ display: flex; flex-direction: column; gap: 10px; margin-bottom: 28px; padding-bottom: 24px; border-bottom: 1px solid var(--border); }}
  .chip {{ font-family: 'IBM Plex Mono', monospace; font-size: 12px; background: var(--surface-2); border: 1px solid var(--border); border-radius: 999px; padding: 3px 10px; }}
  .chip.tag {{ color: var(--accent); }}
  .fact-row {{ display: flex; flex-wrap: wrap; gap: 8px 10px; align-items: baseline; }}
  .fact-row .label {{ font-size: 11.5px; color: var(--ink-dim); width: 70px; flex-shrink: 0; }}
  .save-theme-note {{ font-size: 12.5px; color: var(--ink-dim); margin: 4px 0 0; }}
  .archive-link {{ font-size: 12.5px; color: var(--accent); }}
  .layout {{ display: grid; grid-template-columns: 1fr 1fr; gap: 28px; align-items: start; }}
  @media (max-width: 820px) {{ .layout {{ grid-template-columns: 1fr; }} }}
  .layout img {{ width: 100%; border-radius: 14px; border: 1px solid var(--border); cursor: zoom-in; display: block; }}
  .panel {{ background: var(--surface); border: 1px solid var(--border); border-radius: 14px; padding: 20px 22px; display: flex; flex-direction: column; gap: 16px; }}
  .distillation {{ font-family: 'Fraunces', Georgia, serif; font-style: italic; font-size: 17px; border-left: 3px solid var(--accent); padding-left: 14px; color: var(--ink); }}
  .reading-text {{ font-size: 14px; color: var(--ink-dim); }}
  .badge {{ display: inline-block; font-family: 'IBM Plex Mono', monospace; font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase; border-radius: 5px; padding: 2px 8px; background: var(--accent-soft); color: var(--accent); width: fit-content; }}
  .meta-block {{ margin-top: 18px; display: flex; flex-direction: column; gap: 12px; }}
  .meta-row {{ display: flex; flex-wrap: wrap; gap: 6px 8px; align-items: baseline; }}
  .meta-row .label {{ font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--ink-dim); width: 100%; }}
  /* Avoided (last 14): collapsed by default — a full anti-repetition
     list is exactly the kind of clutter nobody needs open by default,
     but should still be there to check. <details>/<summary> gives real
     collapse behavior with no JS; the marker is restyled from the
     browser's default triangle to a +/- box next to the label. */
  .avoided-details summary {{ cursor: pointer; list-style: none; display: flex; align-items: center; gap: 8px; }}
  .avoided-details summary::-webkit-details-marker {{ display: none; }}
  .avoided-details summary .toggle-icon {{ display: inline-flex; align-items: center; justify-content: center; width: 16px; height: 16px; border: 1px solid var(--border); border-radius: 4px; color: var(--accent); font-size: 12px; line-height: 1; flex-shrink: 0; }}
  .avoided-details summary .toggle-icon .minus {{ display: none; }}
  .avoided-details[open] summary .toggle-icon .plus {{ display: none; }}
  .avoided-details[open] summary .toggle-icon .minus {{ display: inline; }}
  .avoided-details summary .label {{ width: auto; }}
  .avoided-details .meta-row {{ margin-top: 8px; }}
  .signature {{ font-size: 12.5px; color: var(--ink-dim); font-style: italic; }}
  .prompt-box {{ background: var(--surface-2); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; font-family: 'IBM Plex Mono', monospace; font-size: 12px; line-height: 1.6; color: var(--ink); margin-top: 20px; }}
  .prompt-label {{ font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-dim); margin-bottom: 6px; }}
  .cost-panel {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; margin-top: 14px; font-family: 'IBM Plex Mono', monospace; font-size: 12px; }}
  .cost-row {{ display: flex; justify-content: space-between; padding: 3px 0; color: var(--ink-dim); }}
  .cost-model {{ color: var(--ink-dim); font-size: 11px; }}
  .cost-note {{ color: var(--ink-dim); font-size: 11px; line-height: 1.5; margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--border); }}
  .cost-row.total {{ color: var(--ink); font-weight: 600; border-top: 1px solid var(--border); margin-top: 4px; padding-top: 6px; }}
  footer {{ font-size: 12px; color: var(--ink-dim); border-top: 1px solid var(--border); padding-top: 16px; margin-top: 32px; }}
  #lightbox {{ position: fixed; inset: 0; background: rgba(0,0,0,0.82); display: none; align-items: center; justify-content: center; padding: 40px; z-index: 10; cursor: zoom-out; }}
  #lightbox.open {{ display: flex; }}
  #lightbox img {{ max-width: min(92vw, 900px); max-height: 90vh; border-radius: 10px; box-shadow: 0 20px 60px rgba(0,0,0,0.5); }}
</style></head><body>
<main>
  <header>
    <span class="eyebrow">Astro-Arc &middot; generation record &middot; <a class="archive-link" href="../../index.html">all generations</a></span>
    <h1>{escaped_label}</h1>
    <div class="fact-row"><span class="label">Natal</span>{natal_chips}</div>
    <div class="fact-row"><span class="label">This arc</span>{arc_chips}</div>
    <p class="save-theme-note">To keep this theme, or share it, open Astro-Arc in the bar and choose it under Themes Generated.</p>
  </header>

  <div class="layout">
    <img id="hero-image" src="{image_src}" alt="Generated image">

    <div class="panel">
      <span class="badge">{narrative_position}</span>
      <p class="distillation">&ldquo;{distillation}&rdquo;</p>
      <p class="reading-text">{reading_text}</p>

      <div class="meta-block">
        <div class="meta-row"><span class="label">Art style</span><span class="chip">{art_style}</span></div>
        <div class="meta-row"><span class="label">Register</span><span class="chip">{register}</span>{secondary_register_chip}</div>
        <div class="meta-row"><span class="label">Constellation</span><span class="chip">{constellation}</span></div>
        <div class="meta-row"><span class="label">Objects</span>{amplification_chips}</div>
        <div class="meta-row"><span class="label">Anomaly</span><span class="chip">{intrusion}</span></div>
        <div class="meta-row"><span class="label">Concept tags</span>{concept_tag_chips}</div>
        <details class="avoided-details">
          <summary><span class="toggle-icon"><span class="plus">+</span><span class="minus">&minus;</span></span><span class="label">Avoided (recent)</span></summary>
          <div class="meta-row">{avoided_chips}</div>
        </details>
        <p class="signature">{visual_signature}</p>
      </div>
    </div>
  </div>

  <div class="prompt-box">
    <div class="prompt-label">Final image prompt</div>
    {final_prompt}
  </div>

  <div class="cost-panel">
    <div class="prompt-label">What this generation cost</div>
    <div class="cost-row"><span>Stage 1 (interpretation) <span class="cost-model">{stage1_model}</span></span><span>{stage1_cost}</span></div>
    <div class="cost-row"><span>Stage 1.5 (amplification) <span class="cost-model">{stage15_model}</span></span><span>{stage15_cost}</span></div>
    <div class="cost-row"><span>Stage 2 (image prompt) <span class="cost-model">{stage2_model}</span></span><span>{stage2_cost}</span></div>
    <div class="cost-row"><span>Visual signature <span class="cost-model">{signature_model}</span></span><span>{signature_cost}</span></div>
    <div class="cost-row"><span>Image render <span class="cost-model">{image_model}{image_quality_note}</span></span><span>{image_cost}</span></div>
    <div class="cost-row total"><span>Total charged this run</span><span>{total_cost}</span></div>
    <div class="cost-note">{cost_note}</div>
  </div>

  <footer>{footer_text}</footer>
</main>
<div id="lightbox"><img id="lightbox-img" src="" alt=""></div>
<script>
  const lightbox = document.getElementById("lightbox");
  const lightboxImg = document.getElementById("lightbox-img");
  document.getElementById("hero-image").addEventListener("click", (e) => {{
    lightboxImg.src = e.target.src;
    lightbox.classList.add("open");
  }});
  lightbox.addEventListener("click", () => lightbox.classList.remove("open"));
  document.addEventListener("keydown", (e) => {{ if (e.key === "Escape") lightbox.classList.remove("open"); }});

</script>
</body></html>
"""


def _esc(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _chip(text, cls=""):
    return f'<span class="chip {cls}">{_esc(text)}</span>'


def natal_and_arc_chips(reading):
    natal = reading["natal"]
    arc = reading["arc"]
    sun = natal["planets"]["sun"]
    moon = natal["planets"]["moon"]
    asc = natal.get("houses", {}).get("ascendant") if natal.get("houses") else None

    natal_chips = [f"Sun in {sun['sign']}", f"Moon in {moon['sign']}"]
    if asc:
        natal_chips.append(f"Ascendant in {asc['sign']}")

    if arc["frequency"] == "daily":
        arc_chips = [f"Moon in {arc['moonSign']}", arc["moonPhase"]]
        t = arc.get("dominantMoonTransit")
        if t:
            arc_chips.append(f"{t['aspect']} natal {t['natalBody'].title()}")
    elif arc["frequency"] == "weekly":
        arc_chips = []
        t = arc.get("dominantTransit")
        if t:
            arc_chips = [f"{t['transitingBody'].title()} {t['aspect']} natal {t['natalBody'].title()}"]
    else:
        arc_chips = [f"Sun in {arc['transitingSun']['sign']}"]
        t = arc.get("dominantOuterTransit")
        if t:
            arc_chips.append(f"{t['transitingBody'].title()} {t['aspect']} natal {t['natalBody'].title()}")

    return (
        "".join(_chip(c) for c in natal_chips),
        "".join(_chip(c) for c in arc_chips),
    )


def _fmt_stage_cost(value):
    """A stage cost of None does NOT mean "unknown" — it means that stage did not
    run, because Stage 1 and Stage 1.5 are cached per period and the visual
    signature is cached per birth chart. Rendering it as a bare dash read as
    missing data and made the total look wrong; "cached" says what actually
    happened."""
    return "cached" if value is None else _fmt_cost(value)


def _cost_note(cost):
    """Spell out what the total does and does not include, since a cached run's
    total is genuinely lower than what the same image would cost from scratch."""
    cached = [
        name for name, key in (
            ("Stage 1", "stage1Cost"), ("Stage 1.5", "stage15Cost"),
            ("the visual signature", "signatureCost"),
        ) if cost.get(key) is None
    ]
    if not cached:
        return "Every stage ran and is billed above."
    joined = cached[0] if len(cached) == 1 else ", ".join(cached[:-1]) + " and " + cached[-1]
    return (
        f"{joined} were reused from cache, so nothing was charged for them on this run — "
        "Stage 1 and Stage 1.5 are cached per period, the signature per birth chart. "
        "Regenerating the same day is cheap for that reason; a first run of a new day "
        "pays for them again."
    )


def _fmt_cost(value):
    if value is None:
        return "—"
    return f"${value:.4f}" if value >= 0.0001 else f"${value:.6f}"


def build(reading_path, meta_path, image_path, label, cost=None, footer_text=None,
          theme_dir=None, period_key=None):
    reading = json.loads(Path(reading_path).read_text())
    meta = json.loads(Path(meta_path).read_text())
    natal_chips, arc_chips = natal_and_arc_chips(reading)
    cost = cost or {}

    concept_tags = meta.get("conceptTags") or []
    avoided = meta.get("avoidedConcepts") or []
    theme_vars = _theme_css_vars(theme_dir)

    created_at = datetime.datetime.now()
    review_id = f"{created_at.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"

    html = PAGE_TEMPLATE.format(
        escaped_label=_esc(label),
        natal_chips=natal_chips,
        arc_chips=arc_chips,
        image_src=IMAGE_REL,
        narrative_position=_esc(meta.get("narrativePosition", "")),
        distillation=_esc(meta.get("distillation", "")),
        reading_text=_esc(meta.get("reading", "")),
        art_style=_esc(_style_label(meta.get("artStyle")) or "unknown"),
        register=_esc(meta.get("register") or "none"),
        # Stage 1.5 metadata (added 2026-09-09). Every field degrades to a
        # placeholder rather than a KeyError so this page still renders for
        # any review logged before the amplification stage existed.
        secondary_register_chip=(_chip(meta["secondaryRegister"]) if meta.get("secondaryRegister") else ""),
        constellation=_esc(meta.get("constellation") or "—"),
        amplification_chips=("".join(_chip(o, "tag") for o in (meta.get("amplificationObjects") or []))
                             or '<span class="chip">—</span>'),
        intrusion=_esc(meta.get("intrusion") or "—"),
        concept_tag_chips="".join(_chip(t, "tag") for t in concept_tags) or '<span class="chip">none</span>',
        avoided_chips="".join(_chip(t) for t in avoided) or '<span class="chip">none yet</span>',
        visual_signature=_esc(meta.get("visualSignature", "")),
        final_prompt=_esc(meta.get("finalPrompt", "")),
        stage1_cost=_fmt_stage_cost(cost.get("stage1Cost")),
        stage15_cost=_fmt_stage_cost(cost.get("stage15Cost")),
        stage2_cost=_fmt_stage_cost(cost.get("stage2Cost")),
        signature_cost=_fmt_stage_cost(cost.get("signatureCost")),
        stage1_model=meta.get("stage1Model") or "",
        stage15_model=meta.get("stage15Model") or "",
        stage2_model=meta.get("stage2Model") or "",
        signature_model=meta.get("signatureModel") or "",
        image_model=cost.get("imageModel") or "",
        image_quality_note=(
            f" · {cost['imageQualityUsed']}" if cost.get("imageQualityUsed") else ""
        ),
        cost_note=_cost_note(cost),
        image_cost=_fmt_cost(cost.get("imageCost")),
        image_cost_source=_esc(cost.get("imageCostSource") or "unknown"),
        total_cost=_fmt_cost(cost.get("totalCost")),
        footer_text=_esc(footer_text or "Generated by Astro-Arc's two-stage LLM pipeline (llm_pipeline.py)."),
        theme_bg=theme_vars["bg"], theme_surface=theme_vars["surface"], theme_surface2=theme_vars["surface2"],
        theme_ink=theme_vars["ink"], theme_ink_dim=theme_vars["inkDim"], theme_accent=theme_vars["accent"],
        theme_border=theme_vars["border"], theme_good=theme_vars["good"],
    )

    out_dir = GENERATIONS_DIR / review_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "index.html"
    out_path.write_text(html)

    # Snapshot the live theme dir (colors.toml, icons.theme, backgrounds/,
    # and anything else that generation's theme generator produced) so a
    # later "Save Selected Theme" has a complete, standalone Omarchy theme
    # to copy out — not just the HTML record. Astro-Arc's own theme dir is
    # wiped clean (except backgrounds/) at the start of every run, so
    # whatever's there when this runs is exactly and only that run's output.
    theme_snapshot_dir = out_dir / "theme"
    if theme_dir and Path(theme_dir).is_dir():
        shutil.copytree(theme_dir, theme_snapshot_dir)
    # The page shows the wallpaper by relative link, so it must be exactly
    # there even if the theme dir held a different background or none.
    image_dst = out_dir / IMAGE_REL
    image_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(image_path, image_dst)

    thumb = None
    try:
        thumbnail_jpeg(image_dst, out_dir / THUMB_REL, THUMB_PX, quality=82)
        thumb = THUMB_REL
    except ImageError:
        pass  # the gallery shows a plain tile; never lose the entry over a thumbnail

    index = json.loads(GENERATIONS_INDEX.read_text()) if GENERATIONS_INDEX.exists() else []
    index.insert(0, {
        "id": review_id,
        "label": label,
        "createdAt": created_at.isoformat(),
        "path": str(out_path),
        "cardCount": 1,
        "periodKey": period_key,
        "hasThemeSnapshot": theme_dir is not None and theme_snapshot_dir.is_dir(),
        "sizeBytes": _dir_size(out_dir),
        # Carried through so Save Selected Theme can suggest a name from
        # this run's actual concept tags instead of a hash-looking id.
        "conceptTags": concept_tags,
        # What the gallery shows for each generation without opening its page.
        "thumb": thumb,
        "distillation": meta.get("distillation") or "",
        "narrativePosition": meta.get("narrativePosition") or "",
        "artStyleLabel": _style_label(meta.get("artStyle")) or "",
    })
    GENERATIONS_INDEX.parent.mkdir(parents=True, exist_ok=True)
    GENERATIONS_INDEX.write_text(json.dumps(index, indent=2))
    archive_gallery.write_gallery()

    return out_path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reading", required=True, help="astro_engine.py output for this generation")
    parser.add_argument("--meta", required=True, help="llm_pipeline.py metadata output for this generation")
    parser.add_argument("--image", required=True, help="the generated background image")
    parser.add_argument("--label", required=True)
    parser.add_argument("--cost-json", help="inline JSON: {stage1Cost, stage2Cost, signatureCost, imageCost, imageCostSource, totalCost}")
    parser.add_argument("--footer")
    parser.add_argument("--theme-dir", help="the live theme dir to snapshot (colors.toml, icons.theme, backgrounds/, ...) for later Save Selected Theme")
    parser.add_argument("--period-key", help="this generation's periodKey, recorded in generations.json")
    args = parser.parse_args()

    cost = json.loads(args.cost_json) if args.cost_json else None
    out_path = build(args.reading, args.meta, args.image, args.label, cost, args.footer,
                      theme_dir=args.theme_dir, period_key=args.period_key)
    print(out_path)


if __name__ == "__main__":
    main()
