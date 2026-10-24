"""The settings schema, through settings.py, astro-arc-config and
astro-arc-generate --check-due. Everything runs against a temporary home.

    python3 -m unittest discover backend/tests
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))

import settings  # noqa: E402

# What astro-arc-config's DEFAULTS string said before the schema; the schema
# must keep saying it, in this order.
SHELL_DEFAULTS = (
    '{"birthDate":"","birthTime":"","birthTimeUnknown":false,"locationName":"","latitude":null,'
    '"longitude":null,"timezone":"","frequency":"manual","intervalHours":1,"imageBackend":"openai",'
    '"openaiModel":"","openaiQuality":"","stage1Model":"gpt-4o-mini","stage2Model":"gpt-4o-mini",'
    '"backgroundSize":"auto","artStyle":"symbolist","themeGenerator":"built-in","backgroundOnly":true,'
    '"animatedBackground":true,"historyRetentionDays":30,"maxCostPerImage":0,"provider":"openai",'
    '"chatReasoning":"default"}')


def quietly(fn, *args, **kwargs):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        result = fn(*args, **kwargs)
    return result, err.getvalue()


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.file = Path(tempfile.mkdtemp()) / "astro-arc.json"
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("ASTRO_ARC_PROVIDER", None)
        os.environ.pop("ASTRO_ARC_REASONING", None)

    def saved(self, data):
        self.file.write_text(json.dumps(data))
        return self.file

    def test_the_defaults_are_the_shell_scripts(self):
        self.assertEqual(settings.dumps(settings.defaults(), indent=None), SHELL_DEFAULTS)

    def test_the_panels_defaults_file_is_generated_from_the_schema(self):
        self.assertEqual(settings.DEFAULTS_FILE.read_text(), settings.defaults_json(),
                         "settings-defaults.json is stale: run backend/bin/settings.py --write-defaults")

    def test_numbers_are_kept_as_written(self):
        self.file.write_text('{"maxCostPerImage": 0.50, "latitude": 40.0, "intervalHours": 3}')
        out = settings.dumps(settings.read(self.file, keep_numbers=True))
        self.assertIn('"maxCostPerImage": 0.50', out)
        self.assertIn('"latitude": 40.0', out)
        self.assertIn('"intervalHours": 3', out)

    def test_dumps_prints_as_jq_does(self):
        value = {"a": "é ✓\x7f", "b": [], "c": {}, "d": [1, {"e": None}], "f": True}
        expected = ('{\n  "a": "é ✓\\u007f",\n  "b": [],\n  "c": {},\n  "d": [\n    1,\n    {\n'
                    '      "e": null\n    }\n  ],\n  "f": true\n}')
        self.assertEqual(settings.dumps(value), expected)
        if shutil.which("jq"):
            jq = subprocess.run(["jq", "."], input=json.dumps(value), capture_output=True, text=True).stdout
            self.assertEqual(settings.dumps(value), jq.rstrip("\n"))

    def test_a_value_that_does_not_fit_reads_as_its_default(self):
        path = self.saved({"frequency": "bogus", "intervalHours": 5, "historyRetentionDays": 0,
                           "backgroundSize": "huge", "birthTimeUnknown": "yes", "latitude": "north",
                           "provider": "azure", "stage2Model": "", "chatReasoning": "loud"})
        config, warnings = quietly(settings.read, path)
        self.assertEqual({k: config[k] for k in ("frequency", "intervalHours", "historyRetentionDays",
                                                 "backgroundSize", "birthTimeUnknown", "latitude",
                                                 "provider", "stage2Model", "chatReasoning")},
                         {"frequency": "manual", "intervalHours": 1, "historyRetentionDays": 30,
                          "backgroundSize": "auto", "birthTimeUnknown": False, "latitude": None,
                          "provider": "openai", "stage2Model": "gpt-4o-mini", "chatReasoning": "default"})
        self.assertEqual(len(warnings.splitlines()), 9)
        self.assertIn("frequency 'bogus' is not valid; using 'manual'", warnings)

    def test_unknown_keys_pass_through(self):
        path = self.saved({"providerModels": {"openrouter": {"stage1Model": "a/b"}}, "captionModel": "x"})
        config = settings.read(path)
        self.assertEqual(config["providerModels"], {"openrouter": {"stage1Model": "a/b"}})
        self.assertEqual(config["captionModel"], "x")

    def test_model_defaults_follow_the_provider(self):
        path = self.saved({"provider": "openrouter"})
        stored, loaded = settings.read(path), settings.load(path)
        self.assertEqual(stored["stage1Model"], "openai/gpt-4o-mini")
        self.assertEqual(stored["openaiModel"], "")
        self.assertEqual(loaded["openaiModel"], "openai/gpt-image-1-mini")
        self.assertEqual(loaded["openaiQuality"], "medium")
        self.assertEqual(settings.load(self.saved({}))["openaiModel"], "gpt-image-1-mini")

    def test_the_harness_overrides_apply_on_load_only(self):
        path = self.saved({"chatReasoning": "none"})
        os.environ["ASTRO_ARC_PROVIDER"] = "openrouter"
        os.environ["ASTRO_ARC_REASONING"] = "low"
        self.assertEqual(settings.read(path)["provider"], "openai")
        loaded = settings.load(path)
        self.assertEqual((loaded["provider"], loaded["chatReasoning"]), ("openrouter", "low"))

    def test_value_reads_a_partial_config(self):
        self.assertEqual(settings.value({}, "stage1Model"), "gpt-4o-mini")
        self.assertEqual(settings.value({"provider": "openrouter"}, "stage1Model"), "openai/gpt-4o-mini")
        self.assertEqual(settings.value({"openaiModel": ""}, "openaiModel"), "gpt-image-1-mini")
        self.assertEqual(settings.value({"frequency": "weekly"}, "frequency"), "weekly")
        self.assertEqual(settings.fixed_size({"backgroundSize": "auto"}), (1600, 900))
        self.assertEqual(settings.fixed_size({"backgroundSize": "2560x1440"}), (2560, 1440))


class CommandLineTest(unittest.TestCase):
    """astro-arc-config and astro-arc-generate --check-due, as processes."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.state = self.home / ".local/state/omarchy/astro-arc"
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("ASTRO_ARC_", "XDG_"))},
                    "HOME": str(self.home), "XDG_DATA_HOME": str(self.home / "share")}

    def run_bin(self, name, *args):
        return subprocess.run([str(BIN / name), *args], env=self.env, capture_output=True, text=True, timeout=60)

    def config(self, *args):
        result = self.run_bin("astro-arc-config", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(self.run_bin("astro-arc-config").stdout)

    def test_a_refused_flag_says_how_and_writes_nothing(self):
        result = self.run_bin("astro-arc-config", "--set-frequency", "often")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.strip(),
                         "Usage: astro-arc-config --set-frequency <hourly|daily|weekly|monthly|manual>")
        self.assertFalse((self.home / ".local/state/omarchy/settings/astro-arc.json").exists())

    def test_a_provider_round_trip_restores_the_models(self):
        self.config("--set-models", "gpt-5.4", "gpt-4.1", "gpt-image-2.5-sunburst", "high")
        away = self.config("--set-provider", "openrouter")
        self.assertEqual(away["provider"], "openrouter")
        self.assertIn("/", away["stage1Model"])
        back = self.config("--set-provider", "openai")
        self.assertEqual([back[k] for k in ("stage1Model", "stage2Model", "openaiModel", "openaiQuality")],
                         ["gpt-5.4", "gpt-4.1", "gpt-image-2.5-sunburst", "high"])

    def test_resolved_is_what_a_run_uses(self):
        stored = json.loads(self.run_bin("astro-arc-config").stdout)
        resolved = json.loads(self.run_bin("astro-arc-config", "--resolved").stdout)
        self.assertEqual(stored["openaiModel"], "")
        self.assertEqual(resolved["openaiModel"], "gpt-image-1-mini")

    @unittest.skipUnless(shutil.which("jq") and shutil.which("flock"), "astro-arc-generate needs jq and flock")
    def test_the_due_check(self):
        def due():
            result = self.run_bin("astro-arc-generate", "--check-due")
            return result.returncode == 0, result.stdout.strip()

        self.assertEqual(due()[0], False)                                    # manual
        self.config("--set-frequency", "daily")
        self.assertIn("no birth date", due()[1])
        self.config("--set-birth", "1990-01-01", "12:00")
        self.assertEqual(due()[0], True)
        today = date.today().isoformat()
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "auto-attempt.json").write_text(json.dumps({"frequency": "daily", "periodKey": today}))
        self.assertEqual(due(), (False, f"Astro-Arc: daily period {today} was already attempted — "
                                        "the next period, or Regenerate, tries again"))
        self.config("--set-frequency", "weekly")
        self.assertEqual(due()[0], True)                                     # attempted under daily only
        (self.state / "last-run.json").write_text(json.dumps({"generatedAt": datetime.now().astimezone().isoformat()}))
        self.assertIn("already generated", due()[1])


if __name__ == "__main__":
    unittest.main()
