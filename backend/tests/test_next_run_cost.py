"""The cost estimate asks the same cache rule the pipeline uses.

next_run_cost reports which stages the next run will be served from cache.
It used to re-derive the rule, without the style, the charter or Stage 1's
distillation, so a style switch was quoted as a cache hit and under-billed.
Each test caches what a run of one configuration would, changes one input,
and checks the estimate against the pipeline's own answer.

    python3 -m unittest discover backend/tests
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))

import llm_pipeline as lp  # noqa: E402
import model_catalog as mc  # noqa: E402
from pipeline_state import PipelineState  # noqa: E402

CONFIG = {"artStyle": "cosmic", "frequency": "daily", "birthDate": "1990-01-01",
          "birthTime": "12:00", "latitude": 40.0, "longitude": -90.0,
          "stage1Model": "gpt-4o-mini", "stage2Model": "gpt-4o-mini"}
READING = {"reading": "A reading.", "distillation": "A distillation.", "narrativePosition": "threshold"}
MEANING = {"constellation": "a constellation", "actorKind": "solitary"}


class NextRunCostTest(unittest.TestCase):
    def setUp(self):
        self.state = PipelineState(Path(tempfile.mkdtemp()))
        self.period = mc._period_key("daily")

    def cache_a_run(self, config=CONFIG, reading=READING):
        """What a run of `config` leaves in the caches."""
        self.state.store_reading(self.period, lp.stage1_cache_key(config), reading)
        self.state.store_meaning(self.period,
                                 lp.meaning_cache_key(config, lp.Catalog.load().style(config["artStyle"])),
                                 MEANING)

    def cached(self, config=CONFIG):
        stages = mc.next_run_cost(settings=config, state=self.state)["stages"]
        return stages["stage1"]["cached"], stages["stage15"]["cached"]

    def test_same_configuration_is_served_both_caches(self):
        self.cache_a_run()
        self.assertEqual(self.cached(), (True, True))

    def test_nothing_cached_is_nothing_served(self):
        self.assertEqual(self.cached(), (False, False))

    def test_a_style_switch_re_runs_the_meaning(self):
        self.cache_a_run()
        self.assertEqual(self.cached(dict(CONFIG, artStyle="ghibli")), (True, False))

    def test_a_charter_edit_re_runs_the_meaning(self):
        self.cache_a_run()
        # The same theme with one more line in its charter, as the next run's
        # catalog would read it after the edit.
        catalog = lp.Catalog.load()
        catalog.style("cosmic")["world"] = dict(catalog.style("cosmic")["world"], myth="an edited line")
        stages = mc.next_run_cost(settings=CONFIG, state=self.state, catalog=catalog)["stages"]
        self.assertEqual((stages["stage1"]["cached"], stages["stage15"]["cached"]), (True, False))

    def test_new_birth_data_re_runs_both(self):
        self.cache_a_run()
        self.assertEqual(self.cached(dict(CONFIG, birthTime="13:00")), (False, False))

    def test_a_schema_bump_re_runs_that_stage(self):
        self.cache_a_run()
        for name, expected in (("STAGE1_SCHEMA_VERSION", (False, True)),
                               ("STAGE15_SCHEMA_VERSION", (True, False))):
            original = getattr(lp, name)
            setattr(lp, name, original + 1)
            try:
                self.assertEqual(self.cached(), expected, name)
            finally:
                setattr(lp, name, original)

    def test_a_reading_without_its_distillation_is_not_cached(self):
        self.cache_a_run(reading={k: v for k, v in READING.items() if k != "distillation"})
        self.assertEqual(self.cached()[0], False)

    def test_the_estimate_agrees_with_the_pipeline(self):
        # The stage itself, given the same state, must make the call the
        # estimate predicted: here a switch to ghibli re-runs the meaning.
        self.cache_a_run()
        config = dict(CONFIG, artStyle="ghibli")
        calls = []

        def chat(model, system, user, slot, temperature=0.8, stage=None):
            calls.append(stage)
            return {"constellation": "another constellation", "movement": "a movement",
                    "affect": "an affect", "actorKind": "solitary", "actor": "an actor"}, None
        lp.stage15_meaning(READING, "stub", self.period, config, state=self.state,
                           dial=lp.dial_from_texture({"polarity": "soft", "intensityBand": "mid",
                                                      "exposureBand": "mid", "multiplicity": 1}),
                           style=lp.Catalog.load().style("ghibli"), chat=chat)
        self.assertEqual(calls, ["stage15"])
        # ...and having re-run it, the next run is served from cache.
        self.assertEqual(self.cached(config), (True, True))


if __name__ == "__main__":
    unittest.main()
