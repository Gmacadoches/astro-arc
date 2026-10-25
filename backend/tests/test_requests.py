"""The exact HTTP requests a chat call and a render send, per provider,
against requests.json. A refactor that changes a URL, a header or a body
field fails here; a change you mean to make is re-pinned with

    python3 backend/tests/test_requests.py --pin

and the diff of requests.json shows exactly what changed. No network and no
keyring: each request is captured where it would be sent.
"""

import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))

import llm_pipeline as lp  # noqa: E402
import model_catalog  # noqa: E402
import openai_image_gen as og  # noqa: E402
import openrouter_image_gen as org  # noqa: E402

SNAPSHOT = Path(__file__).with_name("requests.json")

# What OpenRouter's /endpoints would say about the image model, fixed so the
# render payload does not depend on the cached catalog or the network.
OPENROUTER_IMAGE_INFO = {
    "supported": {"aspect_ratio": {"type": "enum", "values": ["1:1", "16:9", "9:16", "auto"]},
                  "quality": {"type": "enum", "values": ["low", "medium", "high"]}},
    "passthrough": ["openai:moderation"],
    "pricing": [],
}


# OpenRouter's chat listing, fixed for the same reason: the cached one lives in
# the real state folder. "a-model" takes reasoning, and must think a little.
OPENROUTER_CHAT_LISTING = {"chatModels": {"a-model": {
    "reasoning": {"supported_efforts": ["none", "low", "medium", "high"], "mandatory": False}}}}


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Sent(Exception):
    """Raised by the fake network once a render's request is captured."""


def _record(req, timeout):
    return {"method": req.get_method(), "url": req.full_url, "timeout": timeout,
            "headers": dict(sorted(req.header_items())),
            "body": json.loads(req.data) if req.data else None}


def chat_request(config, model="a-model"):
    sent = []

    def fake_open(req, timeout=None):
        sent.append(_record(req, timeout))
        reply = {"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {"prompt_tokens": 1}}
        return _Response(json.dumps(reply).encode())

    with mock.patch.object(lp, "open_no_redirect", fake_open), \
            mock.patch.object(lp, "get_api_key", lambda slot, provider="openai": f"key-{slot}-{provider}"), \
            mock.patch.object(model_catalog, "get_capability", lambda *args, **kwargs: None), \
            mock.patch.object(model_catalog, "load_availability",
                              lambda provider="openai": OPENROUTER_CHAT_LISTING if provider == "openrouter" else None):
        lp.chat_client(config)(model, "SYSTEM", "USER", "stage2", temperature=0.95)
    return sent


def render_request(provider, model, quality="medium", width=3840, height=2160):
    sent = []

    def fake_open(req, timeout=None):
        sent.append(_record(req, timeout))
        raise _Sent

    with mock.patch.object(og, "open_no_redirect", fake_open), \
            mock.patch.object(og, "get_api_key", lambda slot="image", provider="openai": f"key-{slot}-{provider}"), \
            mock.patch.object(org, "model_info", lambda model: OPENROUTER_IMAGE_INFO):
        try:
            og.generate("A PROMPT", "/nonexistent/never.png", model, quality, width, height, provider)
        except _Sent:
            pass
    return sent


def requests():
    """Every pinned case, by name."""
    return {
        "chat openai": chat_request({}),
        "chat openrouter, reasoning default": chat_request({"provider": "openrouter", "chatReasoning": "default"}),
        "chat openrouter, reasoning none": chat_request({"provider": "openrouter", "chatReasoning": "none"}),
        "chat openrouter, reasoning low": chat_request({"provider": "openrouter", "chatReasoning": "low"}),
        "chat openrouter, reasoning minimal": chat_request({"provider": "openrouter", "chatReasoning": "minimal"}),
        "render openai gpt-image-1-mini": render_request("openai", "gpt-image-1-mini"),
        "render openai gpt-image-2": render_request("openai", "gpt-image-2", "high"),
        "render openrouter": render_request("openrouter", "openai/gpt-image-1-mini"),
    }


def dumps(value):
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


class RequestsTest(unittest.TestCase):
    maxDiff = None

    def test_the_requests_are_the_pinned_ones(self):
        pinned = json.loads(SNAPSHOT.read_text())
        actual = json.loads(dumps(requests()))
        self.assertEqual(sorted(actual), sorted(pinned), "the set of cases changed: re-pin")
        for name in pinned:
            with self.subTest(name):
                self.assertEqual(actual[name], pinned[name],
                                 "the request changed; if you meant it, run "
                                 "python3 backend/tests/test_requests.py --pin")


if __name__ == "__main__":
    if sys.argv[1:] == ["--pin"]:
        SNAPSHOT.write_text(dumps(requests()))
        print(f"wrote {SNAPSHOT}")
    else:
        unittest.main()
