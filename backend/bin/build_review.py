#!/usr/bin/env python3
"""Builds a self-contained, local "iteration review" page for one real
Astro-Arc generation — the reading, the prompt, the image, and the tuning
metadata (register, concept tags, visual signature) that produced it — and
logs it so every iteration stays browsable from the widget instead of only
the most recent one being visible.

Every build is saved under
~/.local/state/omarchy/astro-arc/reviews/<id>/index.html, plus a full
theme-dir snapshot at reviews/<id>/theme/ (colors.toml, icons.theme,
backgrounds/, ...) when --theme-dir is given, and registered in
~/.local/state/omarchy/astro-arc/reviews/index.json (newest first) —
including each entry's real on-disk size — which Panel.qml reads to
populate its "Themes Generated" picker. astro-arc-save-theme copies a
review's theme/ snapshot out to a new permanent Omarchy theme; astro-arc-
prune-history deletes entries (and their matching history/<periodKey>.png)
past the configured retention window.

This is called automatically from astro-arc-generate after every real run
— it is not a manual dev-only tool. (An earlier version of this file was
never wired into astro-arc-generate at all, and built its "reading" panel
from symbol_map.py's fixed vocabulary — both wrong since the two-stage LLM
pipeline replaced that lookup-table approach.)

Usage:
    build_review.py --reading reading.json --meta pipeline_meta.json \\
      --image background.png --label "Daily · Sep 6, 1:15 PM" \\
      --backend openai --image-model gpt-image-1-mini
"""

import argparse
import base64
import datetime
import json
import re
import shutil
import tomllib
import uuid
from pathlib import Path

REVIEWS_DIR = Path.home() / ".local/state/omarchy/astro-arc/reviews"
INDEX_FILE = REVIEWS_DIR / "index.json"
STYLES_FILE = Path.home() / ".local/share/omarchy/astro-arc/pipeline/styles.toml"

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


def _humanize_concept_tag(tag):
    """"nature/rootedness" -> "Nature Rootedness" — mirrors Model.js's
    humanizeConceptTag exactly, so the Save Theme button's suggested name
    (computed here, in the static HTML) matches what the widget's own
    Save Selected Theme prompt would have suggested for the same entry."""
    words = [w for w in re.split(r"[/_-]+", str(tag or "")) if w]
    return " ".join(w[:1].upper() + w[1:] for w in words)


def _suggest_theme_name(concept_tags):
    tags = [t for t in (concept_tags or []) if isinstance(t, str) and t.strip()]
    if not tags:
        return ""
    return " ".join(_humanize_concept_tag(t) for t in tags[:2])

PAGE_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<title>Astro-Arc Iteration — {escaped_label}</title>
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
  .save-theme-row {{ display: flex; align-items: center; gap: 12px; margin-top: 4px; }}
  .save-theme-btn {{ font-family: 'IBM Plex Mono', monospace; font-size: 12px; letter-spacing: 0.02em; background: var(--accent-soft); color: var(--accent); border: 1px solid var(--accent); border-radius: 8px; padding: 8px 16px; cursor: pointer; }}
  .save-theme-btn:hover {{ background: var(--accent); color: var(--bg); }}
  .save-theme-status {{ font-size: 12px; color: var(--ink-dim); }}
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
  .cost-row.total {{ color: var(--ink); font-weight: 600; border-top: 1px solid var(--border); margin-top: 4px; padding-top: 6px; }}
  footer {{ font-size: 12px; color: var(--ink-dim); border-top: 1px solid var(--border); padding-top: 16px; margin-top: 32px; }}
  #lightbox {{ position: fixed; inset: 0; background: rgba(0,0,0,0.82); display: none; align-items: center; justify-content: center; padding: 40px; z-index: 10; cursor: zoom-out; }}
  #lightbox.open {{ display: flex; }}
  #lightbox img {{ max-width: min(92vw, 900px); max-height: 90vh; border-radius: 10px; box-shadow: 0 20px 60px rgba(0,0,0,0.5); }}
</style></head><body>
<main>
  <header>
    <span class="eyebrow">Astro-Arc &middot; iteration record</span>
    <h1>{escaped_label}</h1>
    <div class="fact-row"><span class="label">Natal</span>{natal_chips}</div>
    <div class="fact-row"><span class="label">This arc</span>{arc_chips}</div>
    <div class="save-theme-row">
      <button class="save-theme-btn" onclick="saveTheme()">Save Theme</button>
      <span class="save-theme-status" id="save-theme-status"></span>
    </div>
  </header>

  <div class="layout">
    <img id="hero-image" src="data:image/png;base64,{image_b64}" alt="Generated image">

    <div class="panel">
      <span class="badge">{narrative_position}</span>
      <p class="distillation">&ldquo;{distillation}&rdquo;</p>
      <p class="reading-text">{reading_text}</p>

      <div class="meta-block">
        <div class="meta-row"><span class="label">Art style</span><span class="chip">{art_style}</span></div>
        <div class="meta-row"><span class="label">Register</span><span class="chip">{register}</span>{secondary_register_chip}</div>
        <div class="meta-row"><span class="label">Constellation</span><span class="chip">{constellation}</span></div>
        <div class="meta-row"><span class="label">Objects</span>{amplification_chips}</div>
        <div class="meta-row"><span class="label">Intrusion</span><span class="chip">{intrusion}</span></div>
        <div class="meta-row"><span class="label">Concept tags</span>{concept_tag_chips}</div>
        <details class="avoided-details">
          <summary><span class="toggle-icon"><span class="plus">+</span><span class="minus">&minus;</span></span><span class="label">Avoided (last 14)</span></summary>
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
    <div class="prompt-label">Cost of this generation (all 3 API calls)</div>
    <div class="cost-row"><span>Stage 1 (interpretation)</span><span>{stage1_cost}</span></div>
    <div class="cost-row"><span>Stage 1.5 (amplification)</span><span>{stage15_cost}</span></div>
    <div class="cost-row"><span>Stage 2 (image prompt)</span><span>{stage2_cost}</span></div>
    <div class="cost-row"><span>Visual signature{signature_note}</span><span>{signature_cost}</span></div>
    <div class="cost-row"><span>Image render ({image_cost_source})</span><span>{image_cost}</span></div>
    <div class="cost-row total"><span>Total</span><span>{total_cost}</span></div>
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

  // Save Theme: prompts for a name (native browser dialog, pre-filled
  // with a suggestion from this generation's own concept tags — same
  // suggestion the widget's own Save Selected Theme prompt would make),
  // then navigates to a custom astroarc:// link. That's registered on
  // this machine (by install.sh) to astro-arc-save-theme-handler, which
  // actually runs astro-arc-save-theme and reports the result as a
  // desktop notification — this static page has no server of its own to
  // report back into, so the notification is the real confirmation, not
  // the status line below (which can only ever say the link was opened).
  // The browser will ask permission to open the link the first time;
  // that's normal for any custom URI scheme, not a bug.
  function saveTheme() {{
    const suggested = {suggested_name_json};
    const name = prompt("Save this theme as:", suggested);
    if (!name) return;
    const id = {review_id_json};
    document.getElementById("save-theme-status").textContent = "Opening Astro-Arc…";
    window.location.href = "astroarc://save-theme?id=" + encodeURIComponent(id) + "&name=" + encodeURIComponent(name);
  }}
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

    # Computed before the HTML so the Save Theme button can embed them —
    # review_id has to exist before the page that names it in a save link.
    created_at = datetime.datetime.now()
    review_id = f"{created_at.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"

    html = PAGE_TEMPLATE.format(
        escaped_label=_esc(label),
        natal_chips=natal_chips,
        arc_chips=arc_chips,
        image_b64=base64.b64encode(Path(image_path).read_bytes()).decode("ascii"),
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
        stage1_cost=_fmt_cost(cost.get("stage1Cost")),
        stage15_cost=_fmt_cost(cost.get("stage15Cost")),
        stage2_cost=_fmt_cost(cost.get("stage2Cost")),
        signature_cost=_fmt_cost(cost.get("signatureCost")),
        signature_note=" (one-time)" if cost.get("signatureCost") is not None else "",
        image_cost=_fmt_cost(cost.get("imageCost")),
        image_cost_source=_esc(cost.get("imageCostSource") or "unknown"),
        total_cost=_fmt_cost(cost.get("totalCost")),
        footer_text=_esc(footer_text or "Generated by Astro-Arc's two-stage LLM pipeline (llm_pipeline.py)."),
        theme_bg=theme_vars["bg"], theme_surface=theme_vars["surface"], theme_surface2=theme_vars["surface2"],
        theme_ink=theme_vars["ink"], theme_ink_dim=theme_vars["inkDim"], theme_accent=theme_vars["accent"],
        theme_border=theme_vars["border"], theme_good=theme_vars["good"],
        suggested_name_json=json.dumps(_suggest_theme_name(concept_tags)),
        review_id_json=json.dumps(review_id),
    )

    out_dir = REVIEWS_DIR / review_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "index.html"
    out_path.write_text(html)

    # Snapshot the live theme dir (colors.toml, icons.theme, backgrounds/,
    # and anything else that generation's theme generator produced) so a
    # later "Save Selected Theme" has a complete, standalone Omarchy theme
    # to copy out — not just the HTML record. Astro-Arc's own theme dir is
    # wiped clean (except backgrounds/) at the start of every run, so
    # whatever's there when this runs is exactly and only that run's output.
    theme_snapshot_dir = None
    if theme_dir and Path(theme_dir).is_dir():
        theme_snapshot_dir = out_dir / "theme"
        shutil.copytree(theme_dir, theme_snapshot_dir)

    index = json.loads(INDEX_FILE.read_text()) if INDEX_FILE.exists() else []
    index.insert(0, {
        "id": review_id,
        "label": label,
        "createdAt": created_at.isoformat(),
        "path": str(out_path),
        "cardCount": 1,
        "periodKey": period_key,
        "hasThemeSnapshot": theme_snapshot_dir is not None,
        # Real directory size, not an estimate — includes the theme
        # snapshot above (the image itself lives only as base64 inside
        # index.html, never duplicated on disk).
        "sizeBytes": _dir_size(out_dir),
        # Carried through so Save Selected Theme can suggest a name from
        # this run's actual concept tags instead of a hash-looking id.
        "conceptTags": concept_tags,
    })
    INDEX_FILE.write_text(json.dumps(index, indent=2))

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
    parser.add_argument("--period-key", help="this generation's periodKey, so pruning can also remove the matching history/<periodKey>.png")
    args = parser.parse_args()

    cost = json.loads(args.cost_json) if args.cost_json else None
    out_path = build(args.reading, args.meta, args.image, args.label, cost, args.footer,
                      theme_dir=args.theme_dir, period_key=args.period_key)
    print(out_path)


if __name__ == "__main__":
    main()
