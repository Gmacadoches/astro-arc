"""The catalog, through its interface: a fixture theme in a temporary folder,
composed end to end, and validation as a fact about the whole catalog.

    python3 -m unittest discover backend/tests
"""

import contextlib
import io
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import llm_pipeline as lp  # noqa: E402
from test_compose import CONFIG, STAGE1, TEXTURE, StubChat, arc  # noqa: E402

KEY = "fixture-world"
MARKER = "Everything here is made of folded brass."


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.user_dir = self.root / "styles"
        self.user_dir.mkdir()
        self.fixture = self.user_dir / f"{KEY}.toml"
        text = (lp.SHIPPED_STYLES_DIR / "cosmic.toml").read_text()
        text = re.sub(r'(?m)^label = .*$', 'label = "Fixture World"', text, count=1)
        self.fixture.write_text(text.replace("[world]\n", f'[world]\nforbids = "{MARKER}"\n', 1))
        shutil.copy(lp.SHIPPED_STYLES_DIR / "cosmic.jpg", self.fixture.with_suffix(".jpg"))

    def load(self, **kwargs):
        return lp.Catalog.load(user_dir=self.user_dir, **kwargs)

    def edit_fixture(self, old, new):
        text = self.fixture.read_text()
        self.assertIn(old, text)
        self.fixture.write_text(text.replace(old, new, 1))

    def test_a_fixture_theme_composes_end_to_end(self):
        catalog = self.load()
        self.assertEqual(catalog.validate(KEY)[0], [])
        style = catalog.style(KEY)
        chat = StubChat()
        result = lp.compose(STAGE1, arc(), lp.dial_from_texture(TEXTURE), style, CONFIG, [], chat=chat)
        meaning_prompt = next(c["user"] for c in chat.calls if c["stage"] == "stage15")
        self.assertIn(MARKER, meaning_prompt)
        self.assertEqual(result["artStyle"], KEY)
        self.assertEqual(result["artStyleLabel"], "Fixture World")
        self.assertTrue(result["finalPrompt"].endswith(style.suffix_for(result["hasPeople"])))

    def test_a_missing_register_line_is_an_error(self):
        register = self.load().registers[0]
        text = self.fixture.read_text()
        line = next(l for l in text.splitlines() if l.startswith((f'"{register}"', f"{register} =")))
        self.fixture.write_text(text.replace(line + "\n", "", 1))
        errors, _ = self.load().validate(KEY)
        self.assertIn(f"{KEY}: [registers] {register}: required (one line per register in "
                      "registers.toml)", errors)
        with self.assertRaises(lp.PipelineError):
            self.load().require(KEY)

    def test_a_new_register_invalidates_every_theme(self):
        # The verdict reads the registers too: a register added to
        # registers.toml needs a line in every theme.
        pipeline = self.root / "pipeline"
        pipeline.mkdir()
        for name in ("actors.toml", "cliches.toml", "registers.toml"):
            shutil.copy(lp.PIPELINE_DIR / name, pipeline / name)
        text = (pipeline / "registers.toml").read_text()
        (pipeline / "registers.toml").write_text(text + '\nfixture = ["fixture register"]\n')
        catalog = self.load(pipeline_dir=pipeline)
        for key in ("cosmic", KEY):
            self.assertIn(f"{key}: [registers] fixture register: required (one line per register "
                          "in registers.toml)", catalog.validate(key)[0])

    def test_warnings_print_once_per_catalog(self):
        catalog = self.load()
        self.assertTrue(catalog.validate(KEY)[1], "the fixture shares verbs with cosmic")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            catalog.require(KEY)
            catalog.require(KEY)
        once = err.getvalue()
        self.assertTrue(once)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.load().require(KEY)
        self.assertEqual(err.getvalue(), once)
        self.assertEqual(len(once.splitlines()), len(catalog.validate(KEY)[1]))

    def test_a_catalog_is_a_snapshot(self):
        catalog = self.load()
        self.edit_fixture('label = "Fixture World"', 'label = "Renamed World"')
        self.assertEqual(catalog.style(KEY).label, "Fixture World")
        self.assertEqual(self.load().style(KEY).label, "Renamed World")

    def test_a_draft_is_loaded_and_a_shipped_key_is_refused(self):
        draft = self.root / "elsewhere" / "draft-world.toml"
        draft.parent.mkdir()
        shutil.copy(self.fixture, draft)
        catalog = self.load(drafts=[draft])
        self.assertEqual(catalog.style("draft-world").source, "draft")
        shipped_copy = self.root / "elsewhere" / "cosmic.toml"
        shutil.copy(self.fixture, shipped_copy)
        with self.assertRaises(ValueError):
            self.load(drafts=[shipped_copy])

    def test_a_user_file_with_a_shipped_key_is_ignored(self):
        shutil.copy(self.fixture, self.user_dir / "cosmic.toml")
        with contextlib.redirect_stderr(io.StringIO()):
            catalog = self.load()
        self.assertEqual(catalog.style("cosmic").source, "shipped")

    def test_an_unknown_key_resolves_to_the_default(self):
        catalog = self.load()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(catalog.resolve("no-such-world"), lp.DEFAULT_STYLE)
        self.assertEqual(catalog.resolve(KEY), KEY)


if __name__ == "__main__":
    unittest.main()
