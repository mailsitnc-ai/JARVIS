"""JARVIS with no screen: the engine and the control port, with nothing that needs a desktop."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import serve  # noqa: E402


class Settings:
    def get(self, key, default=None):
        return default


class FakeJarvis:
    def __init__(self, reply="Done, sir.", job=None, blow_up=False):
        self.reply = reply
        self.job = job
        self.blow_up = blow_up
        self.asked = []
        self.registry = type("R", (), {"errors": {}, "skills": {}})()

    def submit(self, request):
        self.asked.append(request)
        if self.blow_up:
            raise RuntimeError("the skill fell over")
        return self.reply, self.job


class AskTests(unittest.TestCase):
    def engine(self, **kwargs):
        headless = serve.Headless(Settings())
        headless.jarvis = FakeJarvis(**kwargs)
        return headless

    def test_a_request_is_run_and_the_words_come_back(self):
        headless = self.engine(reply="80 percent, sir.")
        self.assertEqual(headless.ask("what's my battery"), "80 percent, sir.")
        self.assertEqual(headless.jarvis.asked, ["what's my battery"])

    def test_only_the_words_come_back_not_the_whole_reply(self):
        """The engine hands back a Reply with the skill, route and timings on it; a phone wants the
        sentence, not the object printed out."""
        reply = type("Reply", (), {"text": "It's 16:40.", "skill": "current_time", "route": "trigger"})()
        self.assertEqual(self.engine(reply=reply).ask("what time is it"), "It's 16:40.")

    def test_a_skill_that_has_to_be_built_is_waited_for(self):
        headless = self.engine(reply="Building that now...", job=lambda: "Built it, sir.")
        self.assertEqual(headless.ask("invent something"), "Built it, sir.")

    def test_a_failure_is_reported_rather_than_swallowed(self):
        answer = self.engine(blow_up=True).ask("do the thing")
        self.assertIn("went wrong", answer)
        self.assertIn("fell over", answer)

    def test_before_the_engine_is_up_it_says_so_instead_of_crashing(self):
        self.assertIn("starting up", serve.Headless(Settings()).ask("hello"))


class ApprovalTests(unittest.TestCase):
    """Nobody is sitting at an always-on box, so risky things are asked on the phone or refused."""

    def test_it_asks_whoever_is_on_the_phone(self):
        from core import remote
        asked = []

        class Channel:
            def confirm(self, req):
                asked.append(req)
                return "once"

        saved = remote.current
        remote.current = lambda: Channel()
        try:
            self.assertEqual(serve.Headless(Settings()).confirm("delete a file"), "once")
        finally:
            remote.current = saved
        self.assertEqual(asked, ["delete a file"])

    def test_with_no_one_to_ask_it_refuses(self):
        from core import remote
        saved = remote.current
        remote.current = lambda: None
        try:
            self.assertEqual(serve.Headless(Settings()).confirm("wipe the disk"), "deny")
        finally:
            remote.current = saved


class ControlPortTests(unittest.TestCase):
    """The same commands the panel answers, so the keeper and the command line work here too."""

    def setUp(self):
        self.headless = serve.Headless(Settings())
        self.headless.jarvis = FakeJarvis()
        serve.STOP.clear()
        self.addCleanup(serve.STOP.clear)

    def test_ping_answers_and_says_it_has_no_screen(self):
        out = self.headless.on_command("ping")
        self.assertTrue(out["ok"])
        self.assertTrue(out["headless"])

    def test_a_request_can_be_sent_in(self):
        self.assertEqual(self.headless.on_command("do open my notes")["queued"], "open my notes")

    def test_an_empty_request_is_turned_down(self):
        self.assertFalse(self.headless.on_command("do   ")["ok"])

    def test_stop_stops_it(self):
        self.assertTrue(self.headless.on_command("quit")["ok"])
        self.assertTrue(serve.STOP.is_set())

    def test_window_commands_are_answered_politely_not_with_a_crash(self):
        out = self.headless.on_command("show")
        self.assertTrue(out["ok"])
        self.assertIn("no screen", out["note"])

    def test_nonsense_is_refused(self):
        self.assertFalse(self.headless.on_command("make me a sandwich")["ok"])


if __name__ == "__main__":
    unittest.main()
