"""The hold-to-talk page: audio in from the phone, JARVIS's answer back out."""
import json
import unittest
import urllib.error
import urllib.request

from core.talk import TalkServer
from tests.helpers import IsolatedCase


def get(url, data=None, kind="application/octet-stream"):
    """(status, body) - never raises for an error status, so the test can assert on it."""
    request = urllib.request.Request(url, data=data, headers={"Content-Type": kind} if data else {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class AnswerTests(unittest.TestCase):
    """The turn itself, with no HTTP involved."""

    def channel(self, heard="what's my battery", reply="80 percent, sir.", voice=False):
        return TalkServer(lambda text: reply, transcribe=lambda raw: heard, voice=voice)

    def test_what_you_said_is_transcribed_run_and_answered(self):
        out = self.channel().answer(b"x" * 4000)
        self.assertEqual(out["heard"], "what's my battery")
        self.assertEqual(out["text"], "80 percent, sir.")

    def test_saying_jarvis_first_is_allowed_but_not_needed(self):
        asked = []
        server = TalkServer(lambda text: asked.append(text) or "done",
                            transcribe=lambda raw: "Jarvis, what's my battery", voice=False)
        server.answer(b"x" * 4000)
        self.assertEqual(asked, ["what's my battery"])      # the name is stripped off the request

    def test_silence_is_not_sent_off_as_a_request(self):
        asked = []
        server = TalkServer(lambda text: asked.append(text) or "x", transcribe=lambda raw: "  ",
                            voice=False)
        out = server.answer(b"x" * 4000)
        self.assertEqual(asked, [])
        self.assertIn("didn't catch", out["text"])

    def test_a_transcription_failure_is_reported_not_hidden(self):
        def boom(_raw):
            raise RuntimeError("whisper is not installed")

        out = TalkServer(lambda text: "x", transcribe=boom, voice=False).answer(b"x" * 4000)
        self.assertIn("whisper is not installed", out["error"])


class ServerTests(IsolatedCase):
    """The page, the token, and handing the reply's audio over exactly once."""

    def setUp(self):
        super().setUp()
        self.server = TalkServer(lambda text: f"you said {text}", port=0,
                                 transcribe=lambda raw: "hello", voice=False, secret="test-secret")
        self.server.start()
        self.addCleanup(self.server.stop)
        self.base = f"http://127.0.0.1:{self.server.port}"

    def test_the_page_needs_the_secret_link(self):
        self.assertEqual(get(f"{self.base}/")[0], 403)
        self.assertEqual(get(f"{self.base}/?k=wrong")[0], 403)
        status, body = get(f"{self.base}/?k=test-secret")
        self.assertEqual(status, 200)
        self.assertIn(b"hold", body.lower())
        self.assertIn(b"getUserMedia", body)                # it really does record

    def test_talking_to_it_over_http(self):
        status, body = get(f"{self.base}/ask?k=test-secret", data=b"a" * 4000)
        self.assertEqual(status, 200)
        out = json.loads(body)
        self.assertEqual(out["heard"], "hello")
        self.assertEqual(out["text"], "you said hello")

    def test_audio_without_the_secret_is_refused(self):
        self.assertEqual(get(f"{self.base}/ask", data=b"a" * 4000)[0], 403)

    def test_a_reply_recording_is_handed_over_once_then_forgotten(self):
        recording = self.tmp / "hello.wav"
        recording.write_bytes(b"RIFFfake")
        self.server.audio["abc"] = str(recording)
        status, body = get(f"{self.base}/audio/abc?k=test-secret")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"RIFFfake")
        self.assertEqual(get(f"{self.base}/audio/abc?k=test-secret")[0], 404)

    def test_a_huge_upload_is_turned_away(self):
        self.assertEqual(get(f"{self.base}/ask?k=test-secret", data=b"a" * (9 * 1024 * 1024))[0], 413)

    def test_unknown_pages_are_not_served(self):
        self.assertEqual(get(f"{self.base}/secrets?k=test-secret")[0], 404)

class TokenTests(IsolatedCase):
    def test_the_secret_is_made_once_and_kept(self):
        from core import talk
        first = talk.token()
        self.assertGreaterEqual(len(first), 16)
        self.assertEqual(talk.token(), first)               # same link next time JARVIS starts
        self.assertTrue(talk.token_path().exists())


if __name__ == "__main__":
    unittest.main()
