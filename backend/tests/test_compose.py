"""compose(), tested through its interface with a stub chat client.

No network, no keyring, no bill: every model call goes to StubChat. compose()
is given no pipeline state unless a test passes one in a temporary directory.

    python3 -m unittest discover backend/tests
"""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))

import llm_pipeline as lp  # noqa: E402
from pipeline_state import PipelineState  # noqa: E402

STYLE = "cosmic"
CONFIG = {"stage1Model": "stub", "stage2Model": "stub", "captionModel": "stub"}
STAGE1 = {"reading": "A reading.", "distillation": "A distillation.", "narrativePosition": "threshold"}
CATALOG = lp.Catalog.load()
TEXTURE = {"polarity": "soft", "intensityBand": "mid", "exposureBand": "mid", "multiplicity": 1}


def arc(day=1):
    return {"periodKey": f"2026-10-{day:02d}", "frequency": "daily", "texture": TEXTURE}


def _tag(text):
    return hashlib.sha256(text.encode()).hexdigest()[:8]


class StubChat:
    """A chat client (see llm_pipeline.openai_chat) that answers by stage and
    records every call. `caption_passes=False` makes every melodrama check fail."""

    def __init__(self, caption_passes=True):
        self.caption_passes = caption_passes
        self.calls = []

    def __call__(self, model, system_prompt, user_prompt, slot, temperature=0.8, stage=None):
        self.calls.append({"stage": stage, "system": system_prompt, "user": user_prompt})
        n = len(self.calls)
        if stage == "stage15":
            return {"constellation": f"constellation {n}", "movement": "a movement",
                    "affect": "an affect", "actorKind": "solitary", "actor": "an actor",
                    "objects": [f"object {n}a", f"object {n}b", f"object {n}c"],
                    "anomaly": f"highlight {n}", "anomalyType": "scale"}, None
        if stage == "stage2":
            return {"prompt": f"prompt {_tag(user_prompt)}", "conceptTags": [f"tag {n}"],
                    "hasPeople": False}, None
        if stage == "caption":
            return {"caption": "a quiet scene", "motive": not self.caption_passes,
                    "sides": False, "performed": False}, None
        raise AssertionError(f"unexpected stage {stage!r}")

    def stages(self):
        return [c["stage"] for c in self.calls]


class ComposeTest(unittest.TestCase):
    def compose(self, history, chat=None, day=1, **kwargs):
        chat = chat or StubChat()
        dial = lp.dial_from_texture(TEXTURE)
        return lp.compose(STAGE1, arc(day), dial, CATALOG.style(STYLE), CONFIG, history, chat=chat,
                          **kwargs), chat

    def run_days(self, n, **kwargs):
        """n consecutive compositions, each one's history entry inserted the
        way a harness (or production's commit) does."""
        history, results = [], []
        for day in range(1, n + 1):
            result, _ = self.compose(history, day=day, **kwargs)
            history.insert(0, result["historyEntry"])
            results.append(result)
        return results

    def test_draw_recency_advances_across_days(self):
        # An activity does not repeat until every other one has had a turn.
        activities = CATALOG.style(STYLE).verbs["actor_soft"]
        results = self.run_days(len(activities))
        drawn = [r["draws"]["activity"] for r in results]
        self.assertEqual(sorted(drawn), sorted(activities))

    def test_register_rotation_reads_the_returned_entry(self):
        first, second = self.run_days(2)
        self.assertNotEqual(first["register"], second["register"])

    def test_material_avoids_the_previous_entrys_objects(self):
        first, _ = self.compose([])
        _, chat = self.compose([first["historyEntry"]], day=2)
        material_prompt = [c["user"] for c in chat.calls if c["stage"] == "stage15"][-1]
        for obj in first["historyEntry"]["objects"]:
            self.assertIn(obj, material_prompt)

    def test_avoided_concepts_come_from_history_unless_overridden(self):
        first, _ = self.compose([])
        tags = first["historyEntry"]["conceptTags"]
        from_history, _ = self.compose([first["historyEntry"]], day=2)
        overridden, _ = self.compose([first["historyEntry"]], day=2, avoided_concepts=[])
        self.assertEqual(from_history["avoidedConcepts"], tags)
        self.assertEqual(overridden["avoidedConcepts"], [])

    def test_pinned_registers_are_used(self):
        pin = ("botanical", "geological")
        result, chat = self.compose([], registers=pin)
        self.assertEqual((result["register"], result["secondaryRegister"]), pin)
        self.assertEqual(result["historyEntry"]["register"], "botanical")

    def test_pinned_draws_give_the_same_dial_as_drawing_them(self):
        drawn, _ = self.compose([])
        pinned, _ = self.compose([], draws=drawn["draws"])
        self.assertEqual(pinned["dial"], drawn["dial"])

    def test_pinned_meaning_runs_only_the_material_call(self):
        unpinned, chat = self.compose([])
        self.assertEqual(chat.stages().count("stage15"), 2)
        pinned, chat = self.compose([], meaning=unpinned["meaning"])
        self.assertEqual(chat.stages().count("stage15"), 1)
        self.assertEqual(pinned["constellation"], unpinned["constellation"])

    def test_caption_resample_retries_stage2_on_failure(self):
        result, chat = self.compose([], chat=StubChat(caption_passes=False), caption="resample")
        self.assertEqual(chat.stages().count("stage2"), 1 + lp.CAPTION_RESAMPLES)
        self.assertEqual(len(result["captionCheck"]["attempts"]), 1 + lp.CAPTION_RESAMPLES)
        self.assertIs(result["captionCheck"]["passed"], False)

    def test_caption_record_checks_once_without_resampling(self):
        result, chat = self.compose([], chat=StubChat(caption_passes=False), caption="record")
        self.assertEqual(chat.stages().count("stage2"), 1)
        self.assertEqual(len(result["captionCheck"]["attempts"]), 1)

    def test_caption_skip_makes_no_check(self):
        result, chat = self.compose([], caption="skip")
        self.assertNotIn("caption", chat.stages())
        self.assertIsNone(result["captionCheck"])

    def test_history_is_neither_read_nor_written(self):
        # Even given a state, compose() only caches the meaning in it: the
        # history is the caller's.
        root = Path(tempfile.mkdtemp())
        state = PipelineState(root)
        state.append_history({"register": "botanical", "conceptTags": ["saved tag"]})
        history = []
        result, _ = self.compose(history, state=state)
        self.assertEqual(history, [])
        self.assertIn("historyEntry", result)
        self.assertEqual(result["avoidedConcepts"], [])
        self.assertEqual(len(state.history()), 1)
        self.assertEqual(sorted(p.name for p in root.iterdir()), ["amplifications", "history.json"])

    def test_unknown_caption_policy_is_refused(self):
        with self.assertRaises(lp.PipelineError):
            self.compose([], caption="sometimes")


if __name__ == "__main__":
    unittest.main()
