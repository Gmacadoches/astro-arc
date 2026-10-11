#!/usr/bin/env python3
"""Turns one generation's archive page into a single self-contained .html file
— the whole record, image included, in one file you can hand to someone.

A page in the archive is not portable on its own. Since 2026-09-19 it points at
its wallpaper by a relative link (build_review.IMAGE_REL), so copying just the
index.html out of a generation folder produces a page with a broken image. This
inlines that image as a data: URI and removes the two links that only make
sense while the page is sitting in the archive.

It rewrites an already-rendered page rather than re-rendering from metadata,
for two reasons: the meta dict build_review.py was called with is never
persisted (only its output is), and rewriting means the pre-2026-09-19 pages,
which already carried their image inline, pass through correctly with nothing
to do.

What deliberately stays: the reading, the final image prompt, and the cost
panel. This is the record — someone reading it is the whole point. That is the
opposite of astro-arc-export-theme's rule, and for a reason: an exported theme
is made for strangers, this is made for someone you chose.

What is NOT in it, and never was: birth date, birth time, location,
coordinates, exact natal degrees, your name, your home path. The natal chips
carry the zodiac sign only (build_review.natal_and_arc_chips); the birth data
itself lives in STATE_DIR and the config file, which this never reads.

Usage: share_record.py <generation-dir> [-o out.html]
       (astro-arc-share-record is the front door — it resolves an id and asks
       where to put the file.)
"""

import argparse
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_review import IMAGE_REL  # noqa: E402

# Both are written by build_review.py's PAGE_TEMPLATE, verbatim and unescaped,
# so matching them as literals is exact rather than a guess at what the HTML
# might look like. Both are also OPTIONAL: the archive is older than either of
# them (98 of the 111 pages here predate the archive link, and the oldest call
# themselves an "iteration record" in the eyebrow), so a page without them is
# an old page, not a broken one. The invariant share() actually enforces is
# that the finished file contains its image.
ARCHIVE_LINK = ('&middot; <a class="archive-link" href="../../index.html">'
                'all generations</a>')
SAVE_NOTE = ('<p class="save-theme-note">To keep this theme, or share it, open '
             'Astro-Arc in the bar and choose it under Themes Generated.</p>')


class ShareError(Exception):
    pass


def _inline_image(page, gen_dir):
    """Replace the relative wallpaper link with the image itself, base64'd into
    a data: URI. A page written before 2026-09-19 has no such link because it
    already carries its image inline; anything else has no image at all, which
    is the one thing a record cannot be sent without."""
    marker = f'src="{IMAGE_REL}"'
    if marker not in page:
        if "data:image/" in page:
            return page
        raise ShareError(f"{gen_dir / 'index.html'} has no image — neither a link "
                         f"to {IMAGE_REL} nor one embedded in the page")
    image = gen_dir / IMAGE_REL
    if not image.is_file():
        raise ShareError(f"the page links {IMAGE_REL} but there is no such file "
                         f"in {gen_dir} — the generation is incomplete")
    encoded = base64.b64encode(image.read_bytes()).decode("ascii")
    # No downscaling. A record holds exactly one image, and the point of
    # sending it is that the recipient sees what was actually made.
    return page.replace(marker, f'src="data:image/png;base64,{encoded}"', 1)


def share(gen_dir):
    """The shareable page for the generation folder `gen_dir`, as a string."""
    gen_dir = Path(gen_dir)
    source = gen_dir / "index.html"
    if not source.is_file():
        raise ShareError(f"no page at {source}")
    page = source.read_text(encoding="utf-8")

    page = _inline_image(page, gen_dir)

    # "all generations" points two levels up, at the archive's gallery. Outside
    # the archive it goes nowhere, so it comes out rather than travelling
    # broken.
    page = page.replace(ARCHIVE_LINK, "", 1)

    # Instructions addressed to the person who owns the bar widget. The
    # recipient has neither the widget nor the theme.
    page = page.replace(SAVE_NOTE, "", 1)

    return page


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("generation_dir", help="a folder under the archive's generations/")
    parser.add_argument("-o", "--out", help="write here instead of stdout")
    args = parser.parse_args(argv)

    try:
        page = share(args.generation_dir)
    except ShareError as exc:
        print(f"share_record.py: {exc}", file=sys.stderr)
        return 1

    if args.out:
        out = Path(args.out)
        out.write_text(page, encoding="utf-8")
        print(out)
    else:
        sys.stdout.write(page)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
