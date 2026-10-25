"""The provider seam: a run's config decides where its calls go.

No network and no keyring: requests are captured where they would be sent.

    python3 -m unittest discover backend/tests
"""

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cost_estimate  # noqa: E402
import llm_pipeline as lp  # noqa: E402
import openai_image_gen as og  # noqa: E402
import providers  # noqa: E402
import settings  # noqa: E402
from test_compose import STAGE1, TEXTURE, StubChat, arc  # noqa: E402

OPENROUTER = {"provider": "openrouter", "chatReasoning": "none"}


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def capture_chat(chat):
    """Call `chat` with the network and keyring stubbed; the request it sent."""
    sent = {}

    def fake_open(req, timeout=None):
        sent.update(url=req.full_url, headers=dict(req.header_items()), body=json.loads(req.data))
        reply = {"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {"prompt_tokens": 1}}
        return _Response(json.dumps(reply).encode())

    with mock.patch.object(lp, "open_no_redirect", fake_open), \
            mock.patch.object(lp, "get_api_key", lambda slot, provider="openai": f"key-{provider}"):
        chat("a-model", "system", "user", "stage2")
    return sent


class ProvidersTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("ASTRO_ARC_PROVIDER", None)
        os.environ.pop("ASTRO_ARC_REASONING", None)

    def test_the_config_names_the_provider(self):
        self.assertEqual(providers.provider_of({}), "openai")
        self.assertEqual(providers.provider_of({"provider": "openrouter"}), "openrouter")
        self.assertEqual(providers.provider_of({"provider": "bogus"}), "openai")

    def test_the_harness_overrides_apply_when_the_settings_are_loaded(self):
        saved = Path(tempfile.mkdtemp()) / "astro-arc.json"
        saved.write_text(json.dumps({"provider": "openai", "chatReasoning": "none"}))
        self.assertEqual(providers.provider_of(settings.load(saved)), "openai")
        os.environ["ASTRO_ARC_PROVIDER"] = "openrouter"
        os.environ["ASTRO_ARC_REASONING"] = "low"
        loaded = settings.load(saved)
        self.assertEqual(providers.provider_of(loaded), "openrouter")
        self.assertEqual(providers.reasoning_of(loaded), "low")

    def test_reasoning_is_only_openrouters(self):
        self.assertEqual(providers.reasoning_of({"chatReasoning": "none"}), "default")
        self.assertEqual(providers.reasoning_of(OPENROUTER), "none")

    def test_an_effort_moves_to_one_the_model_offers_but_keeps_thinking(self):
        listed = {"reasoning": {"supported_efforts": ["none", "low", "high"]}}
        self.assertEqual(providers.reasoning_for(listed, "minimal"), {"effort": "low"})
        self.assertEqual(providers.reasoning_for(listed, "medium"), {"effort": "low"})
        self.assertEqual(providers.reasoning_for(listed, "none"), {"effort": "none"})
        mandatory = {"reasoning": {"supported_efforts": ["none", "medium"], "mandatory": True}}
        self.assertEqual(providers.reasoning_for(mandatory, "none"), {"effort": "medium"})
        self.assertIsNone(providers.reasoning_for(listed, "default"))
        self.assertIsNone(providers.reasoning_for({}, "low"))

    def test_an_openai_run_uses_the_openai_adapter_itself(self):
        self.assertIs(lp.chat_client({}), lp.openai_chat)

    def test_the_chat_call_goes_where_the_config_says(self):
        openai = capture_chat(lp.chat_client({}))
        self.assertEqual(openai["url"], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(openai["headers"]["Authorization"], "Bearer key-openai")
        self.assertNotIn("X-title", openai["headers"])
        openrouter = capture_chat(lp.chat_client(OPENROUTER))
        self.assertEqual(openrouter["url"], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(openrouter["headers"]["Authorization"], "Bearer key-openrouter")
        self.assertEqual(openrouter["headers"]["X-title"], "Astro-Arc")

    def test_the_saved_setting_does_not_steer_a_pinned_config(self):
        # The provider is a fact about the run's config: a harness on an
        # OpenAI config talks to OpenAI whatever is saved.
        with mock.patch.object(og, "saved_provider", lambda: "openrouter"):
            self.assertEqual(capture_chat(lp.chat_client({}))["url"],
                             "https://api.openai.com/v1/chat/completions")

    def test_compose_defaults_to_the_providers_models(self):
        chat = StubChat()
        models = []
        original = chat.__call__

        def recording(model, *args, **kwargs):
            models.append(model)
            return original(model, *args, **kwargs)
        result = lp.compose(STAGE1, arc(), lp.dial_from_texture(TEXTURE), lp.Catalog.load().style("cosmic"),
                            {"provider": "openrouter"}, [], chat=recording)
        self.assertEqual(set(models), {"openai/gpt-4o-mini"})
        self.assertEqual(result["stage2Model"], "openai/gpt-4o-mini")
        self.assertEqual(result["captionCheck"]["model"], "openai/gpt-4o-mini")

    def test_a_render_goes_where_it_is_told(self):
        calls = []
        with mock.patch("openrouter_image_gen.generate", lambda *a, **k: calls.append(a) or ("out", {})):
            og.generate("prompt", "/tmp/never.png", "openai/gpt-image-1-mini", "low", 1600, 900, "openrouter")
        self.assertEqual(len(calls), 1)

    def test_openrouters_billed_cost_is_the_cost(self):
        self.assertEqual(cost_estimate.chat_call_cost("any/model", {"prompt_tokens": 10, "cost": 0.0123}), 0.0123)
        self.assertIsNone(cost_estimate.chat_call_cost("no-such-model", {"prompt_tokens": 10}))


if __name__ == "__main__":
    unittest.main()
