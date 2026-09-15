import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class FakeLLM:
    """Scripted LLM. Fails the test loudly on any call it was not given an answer for."""

    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []
        self.kwargs = []
        self.last_provider = None

    def order(self):
        return ["fake"]

    def complete(self, prompt=None, **kwargs):
        if prompt is None and kwargs.get("messages"):
            prompt = kwargs["messages"][-1]["content"]
        self.calls.append(prompt)
        self.kwargs.append(kwargs)
        if not self.responses:
            raise AssertionError(f"unexpected LLM call: {str(prompt)[:120]!r}")
        self.last_provider = "fake"
        return self.responses.pop(0)


class IsolatedCase(unittest.TestCase):
    """Private APPDATA, and no JARVIS_* or API key variables from the real environment."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp = Path(self._tmp.name)
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("JARVIS_") and k.upper() != "GROQ_API_KEY"}
        env["APPDATA"] = str(self.tmp / "appdata")
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()
