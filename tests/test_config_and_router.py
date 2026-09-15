import io
import json
import os
import unittest
import urllib.error
from unittest import mock

from core import keystore
from core.config import load_settings, parse_value, read_user_config, set_user_value
from core.llm_router import GroqProvider, HttpResponse, LLMError, LLMRouter, OllamaProvider, ProviderUnavailable
from tests.helpers import IsolatedCase


class FakeProvider:
    def __init__(self, name, available=True, answer=None, error=None):
        self.name, self._available, self.answer, self.error, self.model = name, available, answer, error, "m"

    def available(self):
        return self._available, "ok" if self._available else "down"

    def chat(self, messages, temperature, max_tokens, **options):
        if self.error:
            raise LLMError(self.error)
        return self.answer


class ConfigTests(IsolatedCase):
    def test_env_override_parses_json(self):
        os.environ["JARVIS_LLM__FALLBACK_ORDER"] = '["ollama","groq"]'
        settings = load_settings()
        self.assertEqual(settings.get("llm.fallback_order"), ["ollama", "groq"])
        self.assertEqual(settings.origin("llm.fallback_order"), "environment")

    def test_powershell_stripped_quotes_still_parse(self):
        self.assertEqual(parse_value("[ollama,groq]"), ["ollama", "groq"])
        self.assertEqual(parse_value("false"), False)
        self.assertEqual(parse_value("ctrl+alt+j"), "ctrl+alt+j")

    def test_user_config_layer(self):
        set_user_value("window.hotkey", "ctrl+shift+space")
        settings = load_settings()
        self.assertEqual(settings.get("window.hotkey"), "ctrl+shift+space")
        self.assertEqual(settings.origin("window.hotkey"), "user config")

    def test_storing_a_key_never_pins_the_provider(self):
        keystore.store_key("groq", "gsk_test_value")
        self.assertEqual(keystore.get_key("groq"), "gsk_test_value")
        self.assertEqual(read_user_config(), {})
        settings = load_settings()
        self.assertEqual(settings.get("llm.provider"), "auto")
        self.assertEqual(LLMRouter(settings).order(), ["groq", "gemini", "ollama"])

    def test_all_providers_exist_and_are_unavailable_without_keys(self):
        router = LLMRouter(load_settings())
        self.assertEqual(set(router.providers), {"groq", "gemini", "ollama"})
        for name in ("groq", "gemini"):
            ok, reason = router.providers[name].available()
            self.assertFalse(ok)
            self.assertIn("no API key", reason)

    def test_cloud_chain_falls_through_to_the_first_provider_with_a_key(self):
        keystore.store_key("gemini", "test-key")
        os.environ["GEMINI_API_KEY"] = "test-key"
        router = LLMRouter(load_settings())
        with mock.patch("core.llm_router.http_json",
                        return_value=HttpResponse(200, {"choices": [{"message": {"content": "hi"}}]}, "", {})):
            self.assertEqual(router.complete("hello"), "hi")  # groq has no key -> gemini answers
        self.assertEqual(router.last_provider, "gemini")

    def test_environment_key_wins(self):
        keystore.store_key("groq", "stored")
        os.environ["GROQ_API_KEY"] = "from-env"
        self.assertEqual(keystore.get_key("groq"), "from-env")


class RouterTests(IsolatedCase):
    def test_falls_through_unavailable_and_failing_providers(self):
        os.environ["JARVIS_LLM__FALLBACK_ORDER"] = '["a","b","c"]'
        router = LLMRouter(load_settings(), providers={
            "a": FakeProvider("a", available=False),
            "b": FakeProvider("b", error="boom"),
            "c": FakeProvider("c", answer="hello"),
        })
        self.assertEqual(router.complete("hi"), "hello")
        self.assertEqual(router.last_provider, "c")

    def test_pinned_provider_is_the_only_one_tried(self):
        os.environ["JARVIS_LLM__PROVIDER"] = "b"
        router = LLMRouter(load_settings(), providers={"a": FakeProvider("a", answer="x"), "b": FakeProvider("b", error="down")})
        with self.assertRaises(LLMError):
            router.complete("hi")

    def test_groq_retries_rate_limits_then_answers(self):
        os.environ["GROQ_API_KEY"] = "test"
        sleeps = []
        provider = GroqProvider(load_settings(), sleep=sleeps.append)
        responses = [
            HttpResponse(429, {"error": {"message": "slow down"}}, "", {"retry-after": "1"}),
            HttpResponse(429, None, "", {}),
            HttpResponse(200, {"choices": [{"message": {"content": "hi"}}]}, "", {}),
        ]
        with mock.patch("core.llm_router.http_json", side_effect=lambda *a, **k: responses.pop(0)):
            self.assertEqual(provider.chat([{"role": "user", "content": "x"}], 0.1, 10), "hi")
        self.assertEqual(sleeps, [1.0, 4.0])

    def test_groq_gives_up_after_four_retries_so_fallback_can_run(self):
        os.environ["GROQ_API_KEY"] = "test"
        sleeps = []
        provider = GroqProvider(load_settings(), sleep=sleeps.append)
        with mock.patch("core.llm_router.http_json", return_value=HttpResponse(429, None, "", {})):
            with self.assertRaises(ProviderUnavailable):
                provider.chat([{"role": "user", "content": "x"}], 0.1, 10)
        self.assertEqual(len(sleeps), 4)


class OllamaStreamingTests(IsolatedCase):
    def test_streams_reply_and_drops_think_for_models_without_it(self):
        provider = OllamaProvider(load_settings())
        sent, events = [], []

        def fake_urlopen(request, timeout=None):
            body = json.loads(request.data)
            sent.append(body)
            if "think" in body:
                error = b'{"error": "\\"qwen2.5-coder:1.5b\\" does not support thinking"}'
                raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, io.BytesIO(error))
            return io.BytesIO(b'{"message": {"content": "Hel"}, "done": false}\n'
                              b'{"message": {"content": "lo"}, "done": false}\n{"done": true}\n')

        with mock.patch("core.llm_router.urllib.request.urlopen", side_effect=fake_urlopen), \
                mock.patch("core.llm_router.http_json", return_value=HttpResponse(200, {"models": []}, "", {})):
            text = provider.chat([{"role": "user", "content": "hi"}], 0.1, 50,
                                 json_schema={"type": "object"}, progress=events.append)

        self.assertEqual(text, "Hello")
        self.assertEqual([("think" in body) for body in sent], [True, False])
        self.assertEqual(sent[1]["format"], {"type": "object"})
        self.assertEqual(sent[1]["keep_alive"], "3m")
        self.assertTrue(sent[1]["stream"])
        self.assertTrue(any("reading the request" in e for e in events))

    def test_mid_stream_error_is_reported(self):
        provider = OllamaProvider(load_settings())
        with mock.patch("core.llm_router.urllib.request.urlopen",
                        return_value=io.BytesIO(b'{"error": "model runner crashed"}\n')), \
                mock.patch("core.llm_router.http_json", return_value=HttpResponse(200, {"models": []}, "", {})):
            with self.assertRaisesRegex(LLMError, "runner crashed"):
                provider.chat([{"role": "user", "content": "hi"}], 0.1, 50)


if __name__ == "__main__":
    unittest.main()
