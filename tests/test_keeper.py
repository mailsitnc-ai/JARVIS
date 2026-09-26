"""The keeper: JARVIS must be up without anyone starting it, and the phone channels must come back
by themselves after sleep."""
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import keeper  # noqa: E402


class Settings:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeTalk:
    PORT = 8765
    _ACTIVE = None

    def __init__(self, up=True, tunnel=True, link="https://x.ngrok-free.dev/?k=1&call=1"):
        self.up = up
        self.tunnel = tunnel
        self.link = link
        self.did = []

    def running(self):
        return self.up

    def start(self):
        self.up = True
        self.did.append("start")
        return "Talk to me from your phone: https://192.168.0.2:8765/?k=1\nsecond line"

    def tunnel_healthy(self):
        return self.tunnel

    def public_url(self):
        return self.link if self.tunnel else ""

    def unexpose(self):
        self.did.append("unexpose")
        return "closed"

    def expose(self, port=None, secret=None):
        self.did.append("expose")
        self.tunnel = True
        return f"You can call me from anywhere: {self.link}"


class FakeRemote:
    def __init__(self, up=True):
        self.up = up
        self.did = []

    def running(self):
        return self.up

    def start(self):
        self.did.append("start")
        self.up = True
        return "Watching WhatsApp."


# ---- the watchdog outside the app ----------------------------------------------------------------

class TestGuard(unittest.TestCase):
    """The loop launchd runs. `quiet()` below stands in for the Mac: nothing else is really there,
    and a launch is believed straight away unless a test says otherwise."""

    def quiet(self, answering, launched, up_after_launch=True, present=()):
        return _patched(keeper,
                        answering=lambda timeout=2.0: answering(),
                        processes=lambda: list(present),
                        wait_until_answering=lambda **kwargs: up_after_launch,
                        launch=lambda direct=False: launched.append(direct) or "started JARVIS.app")

    def test_it_leaves_a_running_jarvis_alone(self):
        launched = []
        with self.quiet(lambda: True, launched):
            started = keeper.guard(gap=0, rounds=3, log=lambda _t: None, sleep=lambda _s: None)
        self.assertEqual((started, launched), (0, []))

    def test_it_starts_jarvis_when_it_is_not_answering(self):
        launched = []
        with self.quiet(lambda: False, launched):
            started = keeper.guard(gap=0, rounds=2, log=lambda _t: None, sleep=lambda _s: None)
        self.assertEqual(started, 2)
        self.assertEqual(len(launched), 2)

    def test_it_says_in_the_log_what_it_did(self):
        lines = []
        with self.quiet(lambda: False, []):
            keeper.guard(gap=0, rounds=1, log=lines.append, sleep=lambda _s: None)
        self.assertEqual(len(lines), 1)
        self.assertIn("started JARVIS.app", lines[0])
        self.assertIn("isn't answering", lines[0])

    def test_a_jarvis_that_is_still_waking_up_is_waited_for_not_doubled(self):
        """The engine loads a speech model on the way up and is silent for half a minute. Starting a
        second one in that gap gave two JARVISes fighting over one control port."""
        launched = []
        with self.quiet(lambda: False, launched, present=[999]):
            keeper.guard(gap=0, rounds=keeper.PATIENCE, log=lambda _t: None, sleep=lambda _s: None)
        self.assertEqual(launched, [])

    def test_it_only_says_once_that_it_is_waiting(self):
        lines = []
        with self.quiet(lambda: False, [], present=[999]):
            keeper.guard(gap=0, rounds=keeper.PATIENCE, log=lines.append, sleep=lambda _s: None)
        self.assertEqual(len(lines), 1)
        self.assertIn("starting", lines[0])

    def test_a_jarvis_that_never_answers_is_cleared_out_and_replaced(self):
        launched, cleared, lines = [], [], []
        with self.quiet(lambda: False, launched, present=[999]), \
             _patched(keeper, clear_out=lambda: cleared.append(1) or "cleared out 1"):
            keeper.guard(gap=0, rounds=keeper.PATIENCE + 1, log=lines.append,
                         sleep=lambda _s: None)
        self.assertEqual(len(cleared), 1)
        self.assertEqual(len(launched), 1)
        self.assertTrue(any("stuck" in line for line in lines))

    def test_when_the_app_wrapper_gets_nowhere_it_starts_the_engine_itself(self):
        """A wrapper sitting there with a dead engine inside used to leave JARVIS off for good."""
        ways = []
        with self.quiet(lambda: False, ways, up_after_launch=False):
            keeper.guard(gap=0, rounds=4, log=lambda _t: None, sleep=lambda _s: None)
        self.assertEqual(ways, [False, False, True, True])

    def test_a_launch_that_worked_forgets_the_earlier_failures(self):
        ways = []
        with self.quiet(lambda: False, ways, up_after_launch=True):
            keeper.guard(gap=0, rounds=4, log=lambda _t: None, sleep=lambda _s: None)
        self.assertEqual(ways, [False, False, False, False])   # never escalates while launches work

    def test_it_waits_for_a_new_jarvis_rather_than_assuming_the_worst(self):
        waited = []
        with _patched(keeper, answering=lambda timeout=2.0: False, processes=lambda: [],
                      launch=lambda direct=False: "started",
                      wait_until_answering=lambda **kwargs: waited.append(1) or True):
            keeper.guard(gap=0, rounds=1, log=lambda _t: None, sleep=lambda _s: None)
        self.assertEqual(len(waited), 1)

    def test_waiting_gives_up_once_the_time_is_up(self):
        with _patched(keeper, answering=lambda timeout=2.0: False):
            self.assertFalse(keeper.wait_until_answering(limit=6, step=3, sleep=lambda _s: None))

    def test_waiting_stops_as_soon_as_it_answers(self):
        answers = iter([False, True, False])
        with _patched(keeper, answering=lambda timeout=2.0: next(answers)):
            self.assertTrue(keeper.wait_until_answering(limit=60, step=1, sleep=lambda _s: None))

    def test_it_can_see_a_jarvis_that_is_there_without_answering(self):
        ran = []
        fake = _fake_subprocess(ran, stdout="3161\n3222\n")
        with _patched(keeper, subprocess=fake):
            self.assertEqual(keeper.processes(), [3161, 3222])
        self.assertIn("pgrep", ran[0])
        self.assertTrue(any(keeper.ENGINE in " ".join(argv) for argv in ran))
        self.assertTrue(any(keeper.WRAPPER in " ".join(argv) for argv in ran))

    def test_a_broken_control_channel_is_not_a_running_jarvis(self):
        broken = types.ModuleType("window_manager.ipc")
        def explode(*_a, **_k):
            raise OSError("no socket")
        broken.send = explode
        saved = sys.modules.get("window_manager.ipc")
        sys.modules["window_manager.ipc"] = broken
        try:
            self.assertFalse(keeper.answering(timeout=0.1))
        finally:
            if saved is None:
                del sys.modules["window_manager.ipc"]
            else:
                sys.modules["window_manager.ipc"] = saved


class TestLaunch(unittest.TestCase):
    def test_the_app_bundle_is_preferred_so_mac_permissions_survive(self):
        ran = []
        app = Path("/tmp/JARVIS.app")
        with _patched(keeper, app_bundle=lambda: app,
                      subprocess=_fake_subprocess(ran)):
            answer = keeper.launch()
        self.assertIn("JARVIS.app", answer)
        self.assertIn("open", ran[0])
        self.assertIn(str(app), ran[0])
        self.assertIn("-n", ran[0])        # a NEW instance, not whatever husk is already there

    def test_without_the_app_it_starts_the_engine_itself(self):
        started = []
        fake_cli = types.ModuleType("core.cli")
        fake_cli._spawn_daemon = lambda show=False: started.append(show)
        saved = sys.modules.get("core.cli")
        sys.modules["core.cli"] = fake_cli
        try:
            with _patched(keeper, app_bundle=lambda: None):
                answer = keeper.launch()
        finally:
            if saved is None:
                del sys.modules["core.cli"]
            else:
                sys.modules["core.cli"] = saved
        self.assertEqual(started, [False])
        self.assertIn("engine", answer)


# ---- the supervisor inside the app ---------------------------------------------------------------

class TestServices(unittest.TestCase):
    def test_a_healthy_mac_says_nothing(self):
        talk, remote = FakeTalk(), FakeRemote()
        notes = keeper.services(Settings(**{"talk.enabled": True, "talk.outside": True,
                                            "remote.enabled": True}), talk=talk, remote=remote)
        self.assertEqual(notes, [])
        self.assertEqual(talk.did, [])
        self.assertEqual(remote.did, [])

    def test_it_starts_the_call_page_when_it_is_down(self):
        talk = FakeTalk(up=False, tunnel=False)
        notes = keeper.services(Settings(**{"talk.enabled": True}), talk=talk, remote=FakeRemote())
        self.assertIn("start", talk.did)
        self.assertEqual(len(notes), 1)
        self.assertNotIn("\n", notes[0])          # one line in the log, not a whole speech

    def test_a_dead_tunnel_is_replaced_after_sleep(self):
        talk = FakeTalk(tunnel=False)
        keeper.services(Settings(**{"talk.enabled": True, "talk.outside": True}),
                        talk=talk, remote=FakeRemote())
        self.assertIn("expose", talk.did)

    def test_a_tunnel_that_is_up_but_disconnected_is_closed_first(self):
        talk = FakeTalk(tunnel=True)
        talk.tunnel_healthy = lambda: False       # process alive, connection gone
        keeper.services(Settings(**{"talk.enabled": True, "talk.outside": True}),
                        talk=talk, remote=FakeRemote())
        self.assertEqual(talk.did, ["unexpose", "expose"])

    def test_it_starts_watching_whatsapp_again(self):
        remote = FakeRemote(up=False)
        notes = keeper.services(Settings(**{"remote.enabled": True}), talk=FakeTalk(), remote=remote)
        self.assertIn("start", remote.did)
        self.assertTrue(notes)

    def test_nothing_is_started_that_was_switched_off(self):
        talk, remote = FakeTalk(up=False, tunnel=False), FakeRemote(up=False)
        notes = keeper.services(Settings(), talk=talk, remote=remote)
        self.assertEqual((notes, talk.did, remote.did), ([], [], []))

    def test_a_new_address_is_sent_to_the_phone_once(self):
        sent = []
        talk = FakeTalk(tunnel=False, link="https://new.ngrok-free.dev/?k=1&call=1")
        with _patched(keeper, told_link=lambda: "", remember_told=lambda link: None):
            keeper.services(Settings(**{"talk.enabled": True, "talk.outside": True}),
                            talk=talk, remote=FakeRemote(), notify=sent.append)
        self.assertEqual(sent, ["https://new.ngrok-free.dev/?k=1&call=1"])

    def test_the_same_address_is_not_sent_again(self):
        sent = []
        link = "https://same.ngrok-free.dev/?k=1&call=1"
        talk = FakeTalk(tunnel=False, link=link)
        with _patched(keeper, told_link=lambda: link, remember_told=lambda _l: None):
            keeper.services(Settings(**{"talk.enabled": True, "talk.outside": True}),
                            talk=talk, remote=FakeRemote(), notify=sent.append)
        self.assertEqual(sent, [])


class TestWatchServices(unittest.TestCase):
    def test_it_keeps_checking_and_logs_what_it_fixed(self):
        lines, passes = [], []

        def pass_(settings, talk=None, remote=None, notify=None):
            passes.append(1)
            return ["put the call page back"] if len(passes) == 1 else []

        with _patched(keeper, services=pass_):
            keeper.watch_services(Settings(), gap=0, rounds=3, log=lines.append,
                                  sleep=lambda _s: None)
        self.assertEqual(len(passes), 3)
        self.assertEqual(lines, ["put the call page back"])

    def test_it_backs_off_when_something_will_not_come_up(self):
        waits = []
        with _patched(keeper, services=lambda *a, **k: ["still down"]):
            keeper.watch_services(Settings(), gap=10.0, rounds=4, log=lambda _t: None,
                                  sleep=waits.append)
        self.assertEqual(waits, [20.0, 40.0, 80.0, 160.0])
        self.assertLessEqual(max(waits), keeper.SERVICE_MAX_GAP)

    def test_a_crash_in_one_pass_does_not_end_the_watch(self):
        lines = []

        def explode(*_a, **_k):
            raise RuntimeError("chrome went away")

        with _patched(keeper, services=explode):
            keeper.watch_services(Settings(), gap=0, rounds=2, log=lines.append,
                                  sleep=lambda _s: None)
        self.assertEqual(len(lines), 2)
        self.assertIn("chrome went away", lines[0])


# ---- helpers -------------------------------------------------------------------------------------

class _patched:
    """Swap module attributes for the duration of a test."""

    def __init__(self, module, **attrs):
        self.module = module
        self.attrs = attrs
        self.saved = {}

    def __enter__(self):
        for name, value in self.attrs.items():
            self.saved[name] = getattr(self.module, name, None)
            setattr(self.module, name, value)
        return self.module

    def __exit__(self, *_exc):
        for name, value in self.saved.items():
            setattr(self.module, name, value)
        return False


def _fake_subprocess(ran, stdout=""):
    fake = types.SimpleNamespace()
    fake.run = lambda argv, **kwargs: (ran.append(argv)
                                       or types.SimpleNamespace(returncode=0, stdout=stdout))
    fake.SubprocessError = Exception
    return fake


if __name__ == "__main__":
    unittest.main()
