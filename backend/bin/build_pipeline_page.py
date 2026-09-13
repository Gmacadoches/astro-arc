#!/usr/bin/env python3
"""Renders PIPELINE.md to docs/pipeline.html.

PIPELINE.md is the source. This exists so the shareable page and the file a
fork actually reads can never disagree — they did for about a day, which is
exactly the kind of duplication the complexity pass was supposed to remove.

It is a small renderer for the constructs PIPELINE.md actually uses, not a
general Markdown implementation. That is deliberate: a general one would be a
dependency, and this one fails loudly on anything it does not recognize rather
than silently dropping it.

What it understands, beyond ordinary paragraphs, lists, tables, fenced code,
blockquotes and inline `code`/**bold**/*italic*/[links](url):

    ## 7. Title          a numbered step, drawn on the pipeline rail
    **Runs:** x · **Cost:** y · **Cache:** z · **Knob:** w
                         the four-cell strip under a step's heading; the four
                         fields are required and in that order
    **At a glance:** a · b · c · d
                         the stat tiles under the standfirst; each entry is a
                         value then a label ("$0.028 fresh run")
    <!-- page:skip -->   drop the next section from the page (the ASCII map
                         earns its place in the file and is redundant beside
                         the rail, which draws the same thing)

Usage:
    build_pipeline_page.py            # write docs/pipeline.html
    build_pipeline_page.py --check    # exit 1 if it is out of date
    build_pipeline_page.py --stdout   # print, write nothing
"""

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "PIPELINE.md"
TARGET = ROOT / "docs" / "pipeline.html"

TITLE = "Astro-Arc Pipeline"
EYEBROW = "Astro-Arc · generation pipeline"
FOOTER = (
    "Generated from PIPELINE.md by backend/bin/build_pipeline_page.py — edit the "
    "Markdown, not this page.<br>Costs are measured at the High preset "
    "(gpt-4.1 · gpt-image-2.5-sunburst), not quoted from a rate card."
)

META_FIELDS = ("Runs", "Cost", "Cache", "Knob")
SEP = " · "


# --- inline -----------------------------------------------------------------

def inline(text):
    """Markdown inline spans -> HTML. Code spans are extracted first so their
    contents are never treated as markup."""
    spans = []

    def stash(m):
        spans.append(m.group(1))
        return "\x00%d\x00" % (len(spans) - 1)

    text = re.sub(r"`([^`]+)`", stash, text)
    text = html.escape(text, quote=False)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\*\w])\*([^*]+)\*(?!\w)", r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00",
                  lambda m: "<code>%s</code>" % html.escape(spans[int(m.group(1))], quote=False),
                  text)


# --- block parsing ----------------------------------------------------------

class Reader:
    def __init__(self, lines):
        self.lines, self.i = lines, 0

    def peek(self):
        return self.lines[self.i] if self.i < len(self.lines) else None

    def next(self):
        line = self.peek()
        self.i += 1
        return line

    def done(self):
        return self.i >= len(self.lines)


def parse_meta(line):
    """`**Runs:** x · **Cost:** y · **Cache:** z · **Knob:** w` -> dict, or None."""
    if not line.startswith("**Runs:**"):
        return None
    parts = line.split(SEP)
    if len(parts) != len(META_FIELDS):
        raise SystemExit(
            "PIPELINE.md: a step's meta line needs exactly %d fields separated by '%s'\n  %s"
            % (len(META_FIELDS), SEP.strip(), line)
        )
    out = {}
    for field, part in zip(META_FIELDS, parts):
        prefix = "**%s:**" % field
        if not part.startswith(prefix):
            raise SystemExit("PIPELINE.md: expected %s in\n  %s" % (prefix, line))
        out[field] = part[len(prefix):].strip()
    return out


def render_body(reader, stop_at_heading=True):
    """Blocks until the next heading. Returns HTML."""
    out = []
    while not reader.done():
        line = reader.peek()
        if line is None:
            break
        if stop_at_heading and line.startswith("#"):
            break
        if not line.strip():
            reader.next()
            continue
        if line.startswith("---") and set(line.strip()) == {"-"}:
            reader.next()
            continue

        if line.startswith("```"):
            reader.next()
            code = []
            while not reader.done() and not reader.peek().startswith("```"):
                code.append(reader.next())
            reader.next()
            body = "\n".join(
                re.sub(r"(#.*)$", r'<span class="c">\1</span>', html.escape(c, quote=False))
                for c in code
            )
            out.append("<pre>%s</pre>" % body)
            continue

        if line.startswith("|"):
            rows = []
            while not reader.done() and (reader.peek() or "").startswith("|"):
                rows.append([c.strip() for c in reader.next().strip("|").split("|")])
            head, body = rows[0], [r for r in rows[2:]]
            out.append(
                '<div class="tablewrap"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
                % ("".join("<th>%s</th>" % inline(c) for c in head),
                   "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % inline(c) for c in r) for r in body))
            )
            continue

        if line.startswith("> "):
            quote = []
            while not reader.done() and (reader.peek() or "").startswith("> "):
                quote.append(reader.next()[2:].strip())
            out.append('<p class="pull">%s</p>' % inline(" ".join(quote)))
            continue

        if line.startswith("- "):
            items = []
            while not reader.done() and (reader.peek() or "").startswith(("- ", "  ")):
                nxt = reader.next()
                if nxt.startswith("- "):
                    items.append(nxt[2:].strip())
                else:
                    items[-1] += " " + nxt.strip()
            out.append("<ul>%s</ul>" % "".join("<li>%s</li>" % inline(i) for i in items))
            continue

        para = []
        while not reader.done() and (reader.peek() or "").strip() \
                and not reader.peek().startswith(("#", "|", "```", "> ", "- ")):
            para.append(reader.next().strip())
        out.append("<p>%s</p>" % inline(" ".join(para)))
    return "\n".join(out)


SKIP_MARKER = "<!-- page:skip -->"


def strip_skipped(lines):
    """Drop `<!-- page:skip -->` and the `## ` section it precedes."""
    out, i = [], 0
    while i < len(lines):
        if lines[i].strip() == SKIP_MARKER:
            i += 1
            while i < len(lines) and not lines[i].startswith("## "):
                i += 1
            i += 1  # the heading itself
            while i < len(lines) and not lines[i].startswith("## "):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return out


def build(markdown):
    lines = strip_skipped(markdown.split("\n"))
    r = Reader(lines)

    title = r.next().lstrip("# ").strip()
    standfirst_parts, ledger = [], []
    while not r.done() and not (r.peek() or "").startswith("#"):
        line = r.next()
        if not line.strip():
            continue
        if line.startswith("**At a glance:**"):
            for entry in line[len("**At a glance:**"):].split(SEP):
                value, _, label = entry.strip().partition(" ")
                ledger.append((value, label))
            continue
        if line.startswith("---"):
            continue
        para = [line.strip()]
        while not r.done() and (r.peek() or "").strip() and not r.peek().startswith("#"):
            nxt = r.peek()
            if nxt.startswith("**At a glance:**"):
                break
            para.append(r.next().strip())
        standfirst_parts.append(" ".join(para))

    sections, steps, skip_next = [], [], False
    while not r.done():
        line = r.next()
        if line is None:
            break
        if line.strip() == "<!-- page:skip -->":
            skip_next = True
            continue
        if not line.startswith("## "):
            continue

        heading = line[3:].strip()
        if skip_next:
            skip_next = False
            while not r.done() and not (r.peek() or "").startswith("## "):
                r.next()
            continue

        m = re.match(r"^(\d+)\.\s+(.*)$", heading)
        if m:
            while not r.done() and not (r.peek() or "").strip():
                r.next()
            meta = parse_meta(r.peek() or "")
            if meta is None:
                raise SystemExit("PIPELINE.md: step '%s' has no meta line" % heading)
            r.next()
            steps.append((m.group(1), m.group(2), meta, render_body(r)))
            continue

        # A prose section: body, then any ### / #### subsections under it.
        blocks = [("body", None, render_body(r))]
        while not r.done() and (r.peek() or "").startswith(("### ", "#### ")):
            sub = r.next()
            level = "h3" if sub.startswith("### ") else "h4"
            blocks.append((level, sub.lstrip("# ").strip(), render_body(r)))
        sections.append((heading, blocks))
        if steps and heading != "The map":
            sections[-1] = (heading, blocks)

    return title, standfirst_parts, ledger, steps, sections


# --- HTML -------------------------------------------------------------------

def step_html(number, heading, meta, body):
    stage = ""
    m = re.match(r"^(Stage [\d.]+)\s+—\s+(.*)$", heading)
    if m:
        stage = '<span class="stage">%s</span>' % html.escape(m.group(1))
        heading = m.group(2)
    spends = meta["Cost"] not in ("free", "—")
    cells = "".join(
        '<div class="%s"><dt>%s</dt><dd>%s</dd></div>'
        % ({"Cost": "cost", "Cache": "cache"}.get(f, ""), f, inline(meta[f]))
        for f in META_FIELDS
    )
    return (
        '<li class="step%s">\n<div class="marker"><span>%02d</span></div>\n'
        '<h3>%s%s</h3>\n<dl class="meta">%s</dl>\n%s\n</li>'
        % (" llm" if spends else "", int(number), stage, inline(heading), cells, body)
    )


def section_html(heading, blocks):
    slug = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
    parts = ['<section class="section" id="%s">' % slug, "<h2>%s</h2>" % inline(heading)]
    for kind, sub_heading, body in blocks:
        if kind == "body":
            if body:
                parts.append(body)
            continue
        if kind == "h3":
            tones = {"removed": "removed", "fixed": "fixed", "closed": "closed",
                     "left alone": "kept"}
            tone = next((v for k, v in tones.items() if sub_heading.lower().startswith(k)), None)
            if tone:
                parts.append('<div class="find %s">' % tone)
                parts.append("<h4>%s</h4>" % inline(sub_heading))
                parts.append(body)
                parts.append("</div>")
            else:
                parts.append("<h3>%s</h3>" % inline(sub_heading))
                parts.append(body)
        else:  # h4 — a callout
            fixed = "now one line" in sub_heading or sub_heading.lower().startswith("adding a register is")
            parts.append('<div class="trap%s"><h4>%s</h4>%s</div>'
                         % (" fixed-trap" if fixed else "", inline(sub_heading), body))
    parts.append("</section>")
    return "\n".join(parts)


def render(markdown, css):
    title, standfirst, ledger, steps, sections = build(markdown)

    tiles = "".join(
        "<div><dt>%s</dt><dd>%s</dd></div>" % (html.escape(label), html.escape(value))
        for value, label in ledger
    )

    body = [
        '<div class="wrap">',
        '<header class="mast">',
        '<div class="eyebrow">%s</div>' % html.escape(EYEBROW),
        "<h1>%s</h1>" % inline(title),
    ]
    for i, para in enumerate(standfirst):
        body.append('<p class="%s">%s</p>' % ("standfirst" if i == 0 else "standfirst sub", inline(para)))
    if tiles:
        body.append('<dl class="ledger">%s</dl>' % tiles)
    body.append("</header>")

    flow = '<ol class="flow">%s</ol>' % "\n".join(step_html(*s) for s in steps)
    placed = False
    for heading, blocks in sections:
        chunk = section_html(heading, blocks)
        if not placed and heading.lower() == "the pipeline":
            chunk = chunk.replace("</section>", flow + "\n</section>")
            placed = True
        body.append(chunk)
    if steps and not placed:
        raise SystemExit("PIPELINE.md: numbered steps exist but there is no '## The pipeline' "
                         "section to put them in")

    body.append("<footer>%s</footer>" % FOOTER)
    body.append("</div>")

    return "<title>%s</title>\n%s\n<style>\n%s</style>\n\n%s\n" % (
        html.escape(TITLE), FONTS, css, "\n".join(body)
    )


FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    "family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&"
    'family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;1,6..72,400&display=swap">'
)


def main(argv):
    css = (Path(__file__).resolve().parent / "pipeline_page.css").read_text(encoding="utf-8")
    out = render(SOURCE.read_text(encoding="utf-8"), css)

    if "--stdout" in argv:
        sys.stdout.write(out)
        return 0
    if "--check" in argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != out:
            print("%s is out of date — run build_pipeline_page.py" % TARGET.relative_to(ROOT))
            return 1
        print("%s is up to date with PIPELINE.md" % TARGET.relative_to(ROOT))
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(out, encoding="utf-8")
    print("wrote %s (%d steps, %d sections)" % (TARGET.relative_to(ROOT),
                                                out.count('class="step'), out.count("<h2>")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
