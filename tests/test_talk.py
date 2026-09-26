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


class _fake_http:
    """Just enough of urlopen's return value to be used in a `with`."""

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self.body

    def __exit__(self, *_exc):
        return False


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

    def test_the_phone_can_ask_whether_the_mac_is_even_awake(self):
        """The page asks this the moment it opens, so a sleeping Mac says so instead of looking
        like a broken call."""
        self.assertEqual(get(f"{self.base}/alive?k=test-secret")[0], 200)
        self.assertEqual(get(f"{self.base}/alive")[0], 403)
        self.assertIn(b"/alive", get(f"{self.base}/?k=test-secret")[1])

    def test_a_request_kept_by_the_phone_is_carried_out_in_words(self):
        """What your phone took down while the Mac was asleep, handed over when it wakes."""
        status, body = get(f"{self.base}/say?k=test-secret", data=b"open my notes",
                           kind="text/plain")
        self.assertEqual(status, 200)
        out = json.loads(body)
        self.assertEqual(out["heard"], "open my notes")
        self.assertEqual(out["text"], "you said open my notes")

    def test_a_kept_request_needs_the_secret_too(self):
        self.assertEqual(get(f"{self.base}/say", data=b"open my notes")[0], 403)

    def test_an_empty_handover_is_not_treated_as_a_request(self):
        self.assertIn("error", json.loads(get(f"{self.base}/say?k=test-secret", data=b" ")[1]))

    def test_the_page_can_answer_on_the_phone_when_this_mac_is_away(self):
        page = get(f"{self.base}/?k=test-secret")[1].decode()
        self.assertIn("webkitSpeechRecognition", page)     # the phone's own ear
        self.assertIn("SpeechSynthesisUtterance", page)    # and its own voice
        self.assertIn("api.groq.com", page)
        self.assertIn("LAPTOP:", page)                     # what it must leave for the Mac
        self.assertIn("jarvis-queue", page)
        self.assertNotIn("__GROQ_MODEL__", page)           # the model name is filled in

    def test_the_mac_never_hands_the_phone_a_key(self):
        """The key is pasted into the phone by hand and kept there - this Mac does not serve it."""
        from core import keystore
        page = get(f"{self.base}/?k=test-secret")[1].decode()
        self.assertNotIn("/setup", page)
        for name in ("groq", "gemini"):
            key = keystore.get_key(name)
            if key:
                self.assertNotIn(key, page)
        self.assertEqual(get(f"{self.base}/setup?k=test-secret")[0], 404)

    def test_the_worker_keeps_a_copy_so_the_icon_opens_with_the_mac_off(self):
        worker = get(f"{self.base}/sw.js?k=test-secret")[1].decode()
        self.assertIn("caches.open", worker)
        self.assertIn("caches.match", worker)
        self.assertIn("fresh.ok", worker)      # ngrok's 404 page must not be kept as the page

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

    def test_the_page_brings_a_worker_that_skips_the_warning_page(self):
        status, body = get(f"{self.base}/sw.js?k=test-secret")
        self.assertEqual(status, 200)
        self.assertIn(b"ngrok-skip-browser-warning", body)
        page = get(f"{self.base}/?k=test-secret")[1]
        self.assertIn(b"serviceWorker.register", page)

    def test_the_icons_android_insists_on_are_there(self):
        """Chrome refuses to install an app without a 192px AND a 512px icon - and it only says
        "this app cannot be installed", never why."""
        manifest = json.loads(get(f"{self.base}/manifest.webmanifest?k=test-secret")[1])
        sizes = {icon["sizes"] for icon in manifest["icons"]}
        self.assertIn("192x192", sizes)
        self.assertIn("512x512", sizes)
        for icon in manifest["icons"]:
            status, body = get(f"{self.base}{icon['src']}")
            self.assertEqual(status, 200, icon["src"])
            self.assertTrue(body.startswith(b"\x89PNG"), icon["src"])
            self.assertEqual(icon["type"], "image/png")
        self.assertFalse(manifest["prefer_related_applications"])

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


class CallerWaitingTests(unittest.TestCase):
    """Someone on the phone can't click a permission box on the Mac - so nothing may block on one."""

    def test_the_thread_answering_a_call_is_marked_as_such(self):
        from core import talk
        seen = []
        server = TalkServer(lambda text: seen.append(talk.on_a_call()) or "done",
                            transcribe=lambda raw: "what is the time", voice=False, port=0)
        self.assertFalse(talk.on_a_call())
        server.answer(b"x" * 4000)
        self.assertEqual(seen, [True])          # the skill runs knowing it's a call
        self.assertFalse(talk.on_a_call())      # and the mark is cleared afterwards

    def test_the_mark_is_cleared_even_when_the_skill_blows_up(self):
        from core import talk

        def boom(_text):
            raise RuntimeError("no")

        server = TalkServer(boom, transcribe=lambda raw: "hello", voice=False, port=0)
        with self.assertRaises(RuntimeError):
            server.answer(b"x" * 4000)
        self.assertFalse(talk.on_a_call())


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

    def test_your_own_address_is_used_when_one_is_set_up(self):
        """A free tunnel's address changes every time; a fixed one keeps the phone's icon working."""
        talk = self.talk
        started = "t=2026-09-25 lvl=info msg=\"started tunnel\" url=https://jarvis-test.ngrok-free.app"
        with unittest.mock.patch.object(talk, "fixed_address", lambda: "jarvis-test.ngrok-free.app"), \
             unittest.mock.patch.object(talk, "_ngrok_ready", lambda: True), \
             self.fake_tunnel(url=started):
            answer = talk.expose(8765, "sekret", wait=6)
        self.assertIn("https://jarvis-test.ngrok-free.app/?k=sekret&call=1", answer)
        self.assertIn("doesn't change", answer)
        self.assertEqual(talk.public_url(), "https://jarvis-test.ngrok-free.app/?k=sekret&call=1")

    def test_without_a_signed_in_ngrok_it_falls_back_to_the_throwaway_address(self):
        talk = self.talk
        with unittest.mock.patch.object(talk, "fixed_address", lambda: "jarvis-test.ngrok-free.app"), \
             unittest.mock.patch.object(talk, "_ngrok_ready", lambda: False), \
             unittest.mock.patch.object(talk, "tunnel_cli", lambda: "/bin/echo"), self.fake_tunnel():
            answer = talk.expose(8765, "sekret", wait=6)
        self.assertIn("trycloudflare.com", answer)

    def test_the_fixed_address_comes_from_your_settings(self):
        from core.config import set_user_value
        self.assertEqual(self.talk.fixed_address(), "")
        set_user_value("talk.address", "jarvis-shivam.ngrok-free.app")
        self.assertEqual(self.talk.fixed_address(), "jarvis-shivam.ngrok-free.app")

    def test_no_tunnel_at_all_is_not_healthy(self):
        self.assertFalse(self.talk.tunnel_healthy())

    def test_a_throwaway_tunnel_counts_as_healthy_while_its_process_lives(self):
        with unittest.mock.patch.object(self.talk, "tunnel_cli", lambda: "/bin/echo"), self.fake_tunnel():
            self.talk.expose(8765, "sekret", wait=6)
        self.assertTrue(self.talk.tunnel_healthy())

    def test_your_own_address_is_checked_against_what_ngrok_is_really_serving(self):
        """ngrok can live through the Mac sleeping with its connection long gone - so ask its own
        agent, on this machine, what it is actually publishing."""
        import io

        with unittest.mock.patch.object(self.talk, "public_url",
                                        lambda: "https://mine.ngrok-free.dev/?k=1"), \
             unittest.mock.patch.object(self.talk, "fixed_address", lambda: "mine.ngrok-free.dev"):
            def agent(body):
                return unittest.mock.patch("urllib.request.urlopen",
                                           lambda *a, **k: _fake_http(io.BytesIO(body)))

            with agent(b'{"tunnels": [{"public_url": "https://mine.ngrok-free.dev"}]}'):
                self.assertTrue(self.talk.tunnel_healthy())
            with agent(b'{"tunnels": []}'):
                self.assertFalse(self.talk.tunnel_healthy())
            with agent(b'{"tunnels": [{"public_url": "https://someone-else.ngrok-free.dev"}]}'):
                self.assertFalse(self.talk.tunnel_healthy())

    def test_an_unreachable_agent_falls_back_to_the_process_being_alive(self):
        """If ngrok's local agent can't be asked, don't tear down a tunnel that may well be fine."""
        def refuse(*_a, **_k):
            raise OSError("connection refused")

        with unittest.mock.patch.object(self.talk, "public_url",
                                        lambda: "https://mine.ngrok-free.dev/?k=1"), \
             unittest.mock.patch.object(self.talk, "fixed_address", lambda: "mine.ngrok-free.dev"), \
             unittest.mock.patch("urllib.request.urlopen", refuse):
            self.assertTrue(self.talk.tunnel_healthy())

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
