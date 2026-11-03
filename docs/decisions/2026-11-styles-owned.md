# Every style is the user's own (Nov 2026)

Supersedes decisions 5 ("(custom)" user themes beside shipped ones) and 6
(core themes decided case by case) of `2026-10-styles.md`, and settles 10.

One session in October made 30 styles, and many of them are as good as the
seven that shipped. Keeping seven "core" styles read-only and everything else
"custom" made the good new ones second-class: no tweak for a shipped style,
no preview change, and no way to see the 30 without making them yourself.

1. **Every style is a file in `~/.config/astro-arc/styles/`, owned by the
   user.** The 37 styles in `backend/pipeline/styles/` are seeds:
   `style_seeds.py` copies each in once, before the panel lists the styles
   and before every run. A run reads only the user's folder; the seeds are
   read directly by tests and the prompt audit, so a machine's edits never
   move the pin. The "(custom)" tag and every shipped-only rule are gone:
   Tweak, Fix, Change preview and delete work on every style.
2. **Ten start in view.** `starter-styles.toml` names them: Needle-Felt Wool
   Diorama, Marbled Paper, Ukiyo-e, Antique Engraving, Cosmic / Nebula, 16-Bit
   Pixel Art, Art Nouveau Poster, Claymation Diorama, Chinese Ink Wash
   Mountains, Coral Reef Tapestry. The other 27 are seeded hidden, so "Show
   hidden" is how a user finds the rest; nothing needs downloading. The
   selected style is never seeded hidden. A file already in the folder under
   a seed's key is adopted, never overwritten, and keeps its visibility.
3. **The default style is Marbled Paper** (was Symbolist, now seeded hidden;
   a config that selected it keeps it).
4. **Updates reach unchanged files only.** `.seeds.json` (in the styles folder,
   so deleting the folder starts over) records each seed file's hash as last
   copied. A release's new seed replaces the user's file only while the file
   still has that hash. The charter and the preview are tracked apart: a new
   preview doesn't stop charter fixes. A changed starter style can be
   reverted to the seed (the current files go to the tweak backup first).
5. **Styles a release adds arrive hidden**, marked new until the Styles view
   has shown them.
6. **Favourites and hidden are settings** (`favoriteStyles`, `hiddenStyles`),
   not fields of the style file: they are the user's view of a world, not the
   world, and a tweak or update to the file never loses them. Starred styles
   list first, each group alphabetical; no manual order.
7. **Delete is for good, and asks for "delete" to be typed.** It removes the
   style's files and its tweak backup. A deleted starter style is never
   seeded again; "Restore deleted starter styles" in the Styles view brings
   them back. The selected style can't be deleted or hidden.
8. **No style names a studio.** `ghibli` / "Studio Ghibli" is now
   `cel-pastoral` / "Hand-Painted Animation", with a suffix that names the
   technique. `starter-styles.toml`'s `[renamed]` moves a config's selection,
   star and hide from the old key. This settles decision 10 without the A/B:
   if recognisability drops, the fix is in the suffix's description of the
   technique, not the name. (The symbolist and cosmic suffixes still name
   historical artists, which rule 5 of the skill allows.)
9. **The website shows every style** from the plugin's own seeds (the site's
   build reads `backend/pipeline/styles/`), the ten first, so it can never
   advertise a style the plugin doesn't have. It is browse-only: installing
   is "Show hidden" in the panel.

Rejected: a copy-on-write override (shipped files stay read-only and a tweak
makes a user copy that wins), which keeps two kinds of style in the code
behind one UI; shipping no styles and installing from the website, which
leaves a fresh install with nothing to generate; a manual drag order.
