#!/usr/bin/env python3
"""Writes the archive's front page: ~/.local/share/astro-arc/index.html.

One static page listing every generation in generations.json, newest first and
grouped by month, each card linking to that generation's own page. It is the
one URL to bookmark: open it in a browser and scroll back through everything
Astro-Arc has ever made. Every link is relative, so the page keeps working if
the archive folder is copied or moved elsewhere, and it needs no server, no
script and no network beyond the web fonts it asks for.

Rewritten whole whenever the archive changes (a new generation, a prune, the
migration), never edited in place.

Usage: archive_gallery.py        rebuild it now
"""

import datetime
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import ARCHIVE_DIR, GALLERY_FILE, GENERATIONS_INDEX  # noqa: E402

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Astro-Arc archive</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,500;1,9..144,400&family=Work+Sans:wght@400;500&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
  :root {{
    --bg: #f4f2ec; --card: #ffffff; --ink: #1d1f29; --dim: #5f6272; --rule: #dcd8cc;
    --accent: #8a6a22; --tile: #e7e3d8;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #111219; --card: #1a1c26; --ink: #e6e4dc; --dim: #9c9eae; --rule: #2b2e3b;
             --accent: #d0a656; --tile: #232633; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--ink);
         font: 15px/1.55 'Work Sans', system-ui, sans-serif; padding: clamp(20px, 4vw, 56px); }}
  main {{ max-width: 1240px; margin: 0 auto; }}
  header {{ border-bottom: 1px solid var(--rule); padding-bottom: 20px; margin-bottom: 8px; }}
  .eyebrow {{ font: 500 11px/1.4 'IBM Plex Mono', monospace; letter-spacing: .12em;
             text-transform: uppercase; color: var(--accent); }}
  h1 {{ font: 500 clamp(28px, 4vw, 42px)/1.1 'Fraunces', Georgia, serif; margin: 6px 0 8px; }}
  .summary {{ color: var(--dim); margin: 0; }}
  h2 {{ font: 500 20px/1.2 'Fraunces', Georgia, serif; margin: 36px 0 14px;
       position: sticky; top: 0; background: var(--bg); padding: 10px 0; z-index: 1; }}
  h2 span {{ font: 12px 'IBM Plex Mono', monospace; color: var(--dim); margin-left: 10px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 22px; }}
  a.card {{ display: flex; flex-direction: column; background: var(--card); border: 1px solid var(--rule);
           border-radius: 10px; overflow: hidden; color: inherit; text-decoration: none;
           transition: transform .15s ease, border-color .15s ease; }}
  a.card:hover, a.card:focus-visible {{ border-color: var(--accent); transform: translateY(-2px); outline: none; }}
  .thumb {{ aspect-ratio: 16 / 9; width: 100%; object-fit: cover; display: block; background: var(--tile); }}
  .blank {{ aspect-ratio: 16 / 9; background: var(--tile); display: grid; place-items: center;
           color: var(--dim); font: 12px 'IBM Plex Mono', monospace; }}
  .body {{ padding: 12px 14px 14px; display: flex; flex-direction: column; gap: 6px; }}
  .meta {{ font: 12px/1.4 'IBM Plex Mono', monospace; color: var(--dim); }}
  .badge {{ color: var(--accent); text-transform: uppercase; letter-spacing: .06em; }}
  .line {{ font: italic 400 15px/1.45 'Fraunces', Georgia, serif; margin: 0; }}
  .empty {{ color: var(--dim); margin-top: 32px; }}
  footer {{ color: var(--dim); font-size: 12.5px; border-top: 1px solid var(--rule);
           margin-top: 48px; padding-top: 16px; }}
  @media (prefers-reduced-motion: reduce) {{ a.card {{ transition: none; }} }}
</style></head>
<body><main>
<header>
  <div class="eyebrow">Astro-Arc</div>
  <h1>Every generation</h1>
  <p class="summary">{summary}</p>
</header>
{sections}
<footer>These are plain files in {archive_dir}. Removing the Astro-Arc plugin leaves them exactly where they are.</footer>
</main></body></html>
"""


def _e(text):
    return html.escape(str(text or ""), quote=True)


def _card(entry):
    gen_id = entry.get("id", "")
    href = f"generations/{gen_id}/index.html"
    thumb = entry.get("thumb")
    picture = (f'<img class="thumb" loading="lazy" src="generations/{_e(gen_id)}/{_e(thumb)}" alt="">'
               if thumb else '<div class="blank">no preview</div>')
    meta = _e(entry.get("label", gen_id))
    position = entry.get("narrativePosition")
    if position:
        meta += f' &middot; <span class="badge">{_e(position)}</span>'
    line = entry.get("distillation")
    line_html = f'<p class="line">&ldquo;{_e(line)}&rdquo;</p>' if line else ""
    return (f'<a class="card" href="{_e(href)}">{picture}'
            f'<div class="body"><div class="meta">{meta}</div>{line_html}</div></a>')


def _month(entry):
    try:
        return datetime.datetime.fromisoformat(entry.get("createdAt", "")).strftime("%B %Y")
    except ValueError:
        return "Undated"


def render(entries):
    if not entries:
        sections = '<p class="empty">Nothing generated yet. Press Generate in the Astro-Arc panel.</p>'
        summary = "No generations yet."
    else:
        groups = {}
        for entry in entries:  # already newest first
            groups.setdefault(_month(entry), []).append(entry)
        sections = "\n".join(
            f'<section><h2>{_e(month)}<span>{len(items)}</span></h2>'
            f'<div class="grid">{"".join(_card(e) for e in items)}</div></section>'
            for month, items in groups.items())
        oldest = entries[-1].get("createdAt", "")[:10]
        summary = f"{len(entries)} generation{'s' if len(entries) != 1 else ''} since {_e(oldest)}. Newest first."
    return PAGE.format(summary=summary, sections=sections, archive_dir=_e(ARCHIVE_DIR))


def write_gallery():
    try:
        entries = json.loads(GENERATIONS_INDEX.read_text()) if GENERATIONS_INDEX.exists() else []
    except (json.JSONDecodeError, OSError):
        entries = []
    GALLERY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = GALLERY_FILE.with_suffix(".html.tmp")
    tmp.write_text(render(entries))
    tmp.replace(GALLERY_FILE)  # never leave a half-written page behind
    return GALLERY_FILE


if __name__ == "__main__":
    print(write_gallery())
