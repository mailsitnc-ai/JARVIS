"""The hold-to-talk page: audio in from the phone, JARVIS's answer back out."""
import json
import unittest
import unittest.mock
import urllib.error
import urllib.request

from core.talk import TalkServer
from tests.helpers import IsolatedCase


def get(url, data=None, kind="application/octet-stream"):
    """(status, body) - never raises for an error status, so the test can assert on it.
    A JARVIS certificate is self-signed, so https is fetched without checking it."""
    import ssl

    request = urllib.request.Request(url, data=data, headers={"Content-Type": kind} if data else {})
    loose = ssl.create_default_context()
    loose.check_hostname = False
    loose.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(request, timeout=10, context=loose) as response:
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
        self.server = TalkServer(lambda text: f"you said {text}", port=0, https=False,
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

class HomeScreenTests(IsolatedCase):
    """"Calling" JARVIS = tapping an icon on the phone that opens straight into a call."""

    def setUp(self):
        super().setUp()
        self.server = TalkServer(lambda text: "hi", port=0, https=False, secret="test-secret",
                                 transcribe=lambda raw: "hello", voice=False)
        self.server.start()
        self.addCleanup(self.server.stop)
        self.base = f"http://127.0.0.1:{self.server.port}"

    def test_the_phone_is_offered_an_app_that_starts_a_call(self):
        status, body = get(f"{self.base}/manifest.webmanifest?k=test-secret")
        self.assertEqual(status, 200)
        manifest = json.loads(body)
        self.assertEqual(manifest["short_name"], "JARVIS")
        self.assertEqual(manifest["display"], "standalone")
        self.assertIn("call=1", manifest["start_url"])         # the icon dials straight in
        self.assertIn("icon.png", manifest["icons"][0]["src"])

    def test_there_is_an_icon(self):
        status, body = get(f"{self.base}/icon.png?k=test-secret")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"\x89PNG"), "not a PNG")

    def test_the_call_link_shows_the_connect_tap(self):
        status, body = get(f"{self.base}/?k=test-secret&call=1")
        self.assertEqual(status, 200)
        self.assertIn(b"tap anywhere to connect", body)


class SecurePageTests(IsolatedCase):
    """A phone only gives its microphone to an https page, so JARVIS serves one with its own cert."""

    def test_the_page_is_served_over_https_with_a_certificate_it_made(self):
        from core.talk import certificate
        server = TalkServer(lambda text: "hi", port=0, transcribe=lambda raw: "hello", voice=False,
                            secret="s3cret", https=True)
        server.start()
        self.addCleanup(server.stop)
        if not server.https:
            self.skipTest("openssl isn't available to make a certificate")
        cert, key = certificate()
        self.assertTrue(cert.exists() and key.exists())
        self.assertTrue(server.url().startswith("https://"), server.url())
        self.assertNotIn("0.0.0.0", server.url())          # the address the phone can actually open
        status, body = get(f"https://127.0.0.1:{server.port}/?k=s3cret")
        self.assertEqual(status, 200)
        self.assertIn(b"End call", body)                   # the page can hold a call open

    def test_the_certificate_names_this_mac_on_the_network(self):
        import subprocess
        from core.talk import certificate, lan_ip
        cert, key = certificate()
        if not cert:
            self.skipTest("openssl isn't available")
        text = subprocess.run(["openssl", "x509", "-in", str(cert), "-noout", "-text"],
                              capture_output=True, text=True, timeout=20).stdout
        self.assertIn(lan_ip(), text)
        self.assertIn("127.0.0.1", text)


class TunnelTests(IsolatedCase):
    """Calling JARVIS from outside: one outbound tunnel publishing just this page - not a VPN."""

    def setUp(self):
        super().setUp()
        from core import talk
        self.talk = talk
        self.addCleanup(talk.unexpose)

    def fake_tunnel(self, url="https://made-up-words-here.trycloudflare.com", dies=False):
        """Stand in for cloudflared: writes the address it was given into its log, like the real one."""
        talk = self.talk

        class FakeProc:
            def __init__(self, *args, **kwargs):
                import os
                self.args = args[0] if args else []
                self.pid = os.getpid()        # a pid that really is alive, like a real tunnel's
                handle = kwargs.get("stdout")
                if not dies and handle is not None:
                    handle.write(f"INF |  {url}  |\n")
                    handle.flush()
                self._done = 0 if dies else None

            def poll(self):
                return self._done

            def terminate(self):
                self._done = 0

            def wait(self, timeout=None):
                return 0

            def kill(self):
                self._done = 0

        return unittest.mock.patch.object(talk.subprocess, "Popen", FakeProc)

    def test_the_outside_address_is_handed_back_with_the_token(self):
        with unittest.mock.patch.object(self.talk, "tunnel_cli", lambda: "/bin/echo"), self.fake_tunnel():
            answer = self.talk.expose(8765, "sekret", wait=6)
        self.assertIn("https://made-up-words-here.trycloudflare.com", answer)
        self.assertIn("k=sekret", self.talk.public_url())
        self.assertIn("call=1", self.talk.public_url())      # opens straight into a call
        self.assertIn("keep it to yourself", answer)         # the warning is part of the answer

    def test_asking_twice_does_not_open_a_second_one(self):
        with unittest.mock.patch.object(self.talk, "tunnel_cli", lambda: "/bin/echo"), self.fake_tunnel():
            self.talk.expose(8765, "sekret", wait=6)
            again = self.talk.expose(8765, "sekret", wait=6)
        self.assertIn("Already reachable", again)

    def test_closing_it_puts_the_page_back_on_your_wifi_only(self):
        with unittest.mock.patch.object(self.talk, "tunnel_cli", lambda: "/bin/echo"), self.fake_tunnel():
            self.talk.expose(8765, "sekret", wait=6)
        self.assertIn("Closed", self.talk.unexpose())
        self.assertEqual(self.talk.public_url(), "")
        self.assertIn("wasn't shared", self.talk.unexpose())

    def test_a_tunnel_that_falls_over_is_reported(self):
        with unittest.mock.patch.object(self.talk, "tunnel_cli", lambda: "/bin/echo"), \
             self.fake_tunnel(dies=True):
            answer = self.talk.expose(8765, "sekret", wait=4)
        self.assertIn("stopped before it was ready", answer)
        self.assertEqual(self.talk.public_url(), "")

    def test_it_says_what_it_needs_when_the_program_is_missing(self):
        with unittest.mock.patch.object(self.talk, "tunnel_cli", lambda: None), \
             unittest.mock.patch.object(self.talk, "install_tunnel", lambda: "no internet"):
            self.assertEqual(self.talk.expose(8765, "sekret", wait=2), "no internet")


class TokenTests(IsolatedCase):
    def test_the_secret_is_made_once_and_kept(self):
        from core import talk
        first = talk.token()
        self.assertGreaterEqual(len(first), 16)
        self.assertEqual(talk.token(), first)               # same link next time JARVIS starts
        self.assertTrue(talk.token_path().exists())


if __name__ == "__main__":
    unittest.main()
