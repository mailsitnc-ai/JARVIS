import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Point JARVIS's data dir (config, API keys, Google login, permissions, usage) at a throwaway folder for
# the WHOLE test run, before any test executes. Without this, tests that store fake keys/config wrote
# straight into the real ~/Library/Application Support/JARVIS on macOS (only %APPDATA% was redirected).
_SESSION_DATA = tempfile.mkdtemp(prefix="jarvis-test-data-")
os.environ["JARVIS_DATA_DIR"] = _SESSION_DATA
os.environ["APPDATA"] = _SESSION_DATA


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
        env["JARVIS_DATA_DIR"] = str(self.tmp / "appdata" / "JARVIS")  # private per test, never the real one
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()
