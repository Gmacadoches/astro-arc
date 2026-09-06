#!/usr/bin/env python3
"""Builds a self-contained, local "iteration review" page for one real
Astro-Arc generation — the reading, the prompt, the image, and the tuning
metadata (register, concept tags, visual signature) that produced it — and
logs it so every iteration stays browsable from the widget instead of only
the most recent one being visible.

Every build is saved under
~/.local/state/omarchy/astro-arc/reviews/<id>/index.html and registered in
~/.local/state/omarchy/astro-arc/reviews/index.json (newest first), which
Panel.qml reads to populate its review-history picker.

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
import uuid
from pathlib import Path

REVIEWS_DIR = Path.home() / ".local/state/omarchy/astro-arc/reviews"
INDEX_FILE = REVIEWS_DIR / "index.json"

PAGE_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<title>Astro-Arc Iteration — {escaped_label}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,600;1,9..144,500&family=Work+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');
  :root {{
    --bg: #f6f4ef; --surface: #ffffff; --surface-2: #efece2; --ink: #1c1e2a;
    --ink-dim: #5d5f72; --accent: #9c7a2e; --accent-soft: #e8dcb8; --border: #ded9c8;
    --good: #3f7a5c;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg: #14161f; --surface: #1c1f2c; --surface-2: #242838; --ink: #e8e6df;
      --ink-dim: #9a9bb0; --accent: #c9a24b; --accent-soft: #3a3320; --border: #333752;
      --good: #7fc9a3;
    }}
  }}
  :root[data-theme="dark"] {{
    --bg: #14161f; --surface: #1c1f2c; --surface-2: #242838; --ink: #e8e6df;
    --ink-dim: #9a9bb0; --accent: #c9a24b; --accent-soft: #3a3320; --border: #333752;
    --good: #7fc9a3;
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
  </header>

  <div class="layout">
    <img id="hero-image" src="data:image/png;base64,{image_b64}" alt="Generated image">

    <div class="panel">
      <span class="badge">{narrative_position}</span>
      <p class="distillation">&ldquo;{distillation}&rdquo;</p>
      <p class="reading-text">{reading_text}</p>

      <div class="meta-block">
        <div class="meta-row"><span class="label">Register</span><span class="chip">{register}</span></div>
        <div class="meta-row"><span class="label">Concept tags</span>{concept_tag_chips}</div>
        <div class="meta-row"><span class="label">Avoided (last 14)</span>{avoided_chips}</div>
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


def build(reading_path, meta_path, image_path, label, cost=None, footer_text=None):
    reading = json.loads(Path(reading_path).read_text())
    meta = json.loads(Path(meta_path).read_text())
    natal_chips, arc_chips = natal_and_arc_chips(reading)
    cost = cost or {}

    concept_tags = meta.get("conceptTags") or []
    avoided = meta.get("avoidedConcepts") or []

    html = PAGE_TEMPLATE.format(
        escaped_label=_esc(label),
        natal_chips=natal_chips,
        arc_chips=arc_chips,
        image_b64=base64.b64encode(Path(image_path).read_bytes()).decode("ascii"),
        narrative_position=_esc(meta.get("narrativePosition", "")),
        distillation=_esc(meta.get("distillation", "")),
        reading_text=_esc(meta.get("reading", "")),
        register=_esc(meta.get("register") or "none"),
        concept_tag_chips="".join(_chip(t, "tag") for t in concept_tags) or '<span class="chip">none</span>',
        avoided_chips="".join(_chip(t) for t in avoided) or '<span class="chip">none yet</span>',
        visual_signature=_esc(meta.get("visualSignature", "")),
        final_prompt=_esc(meta.get("finalPrompt", "")),
        stage1_cost=_fmt_cost(cost.get("stage1Cost")),
        stage2_cost=_fmt_cost(cost.get("stage2Cost")),
        signature_cost=_fmt_cost(cost.get("signatureCost")),
        signature_note=" (one-time)" if cost.get("signatureCost") is not None else "",
        image_cost=_fmt_cost(cost.get("imageCost")),
        image_cost_source=_esc(cost.get("imageCostSource") or "unknown"),
        total_cost=_fmt_cost(cost.get("totalCost")),
        footer_text=_esc(footer_text or "Generated by Astro-Arc's two-stage LLM pipeline (llm_pipeline.py)."),
    )

    created_at = datetime.datetime.now()
    review_id = f"{created_at.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    out_dir = REVIEWS_DIR / review_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "index.html"
    out_path.write_text(html)

    index = json.loads(INDEX_FILE.read_text()) if INDEX_FILE.exists() else []
    index.insert(0, {
        "id": review_id,
        "label": label,
        "createdAt": created_at.isoformat(),
        "path": str(out_path),
        "cardCount": 1,
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
    args = parser.parse_args()

    cost = json.loads(args.cost_json) if args.cost_json else None
    out_path = build(args.reading, args.meta, args.image, args.label, cost, args.footer)
    print(out_path)


if __name__ == "__main__":
    main()
