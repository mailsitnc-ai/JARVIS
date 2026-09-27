"""The Mac's end of the cloud JARVIS: pick up what was left, do it, answer it."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import cloudlink  # noqa: E402


class Calls:
    """Stands in for the cloud: records what was asked of it, answers what it's told to."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.made = []

    def __call__(self, path, data=None, timeout=cloudlink.TIMEOUT):
        self.made.append((path, data))
        answer = self.answers.get(path.split("?")[0])
        return answer(data) if callable(answer) else answer


class swap:
    def __init__(self, **attrs):
        self.attrs = attrs
        self.saved = {}

    def __enter__(self):
        for name, value in self.attrs.items():
            self.saved[name] = getattr(cloudlink, name)
            setattr(cloudlink, name, value)

    def __exit__(self, *_exc):
        for name, value in self.saved.items():
            setattr(cloudlink, name, value)
        return False


JOB = {"id": "j1", "text": "open my notes", "from": "919770094860"}


class TakingWorkTests(unittest.TestCase):
    def test_what_the_cloud_kept_comes_back(self):
        with swap(_call=Calls({"/jobs": {"jobs": [JOB]}})):
            self.assertEqual(cloudlink.waiting(), [JOB])

    def test_nothing_waiting_is_not_an_error(self):
        with swap(_call=Calls({"/jobs": {"jobs": []}})):
            self.assertEqual(cloudlink.waiting(), [])

    def test_a_cloud_that_cannot_be_reached_leaves_it_at_that(self):
        with swap(_call=Calls({"/jobs": None})):
            self.assertEqual(cloudlink.waiting(), [])

    def test_rubbish_from_the_cloud_is_ignored_not_run(self):
        with swap(_call=Calls({"/jobs": {"jobs": ["nonsense", {}, {"text": ""}]}})):
            self.assertEqual(cloudlink.waiting(), [])


class DoingWorkTests(unittest.TestCase):
    def test_a_job_is_carried_out_and_the_answer_sent_back(self):
        calls = Calls({"/jobs": {"ok": True}})
        with swap(_call=calls):
            done = cloudlink.once(lambda text: f"Opened {text}.", jobs=[JOB])
        self.assertEqual(done, 1)
        sent = [data for path, data in calls.made if data]
        self.assertEqual(sent[0]["text"], "Opened open my notes.")
        self.assertEqual(sent[0]["to"], "919770094860")

    def test_a_job_that_blows_up_still_gets_answered(self):
        calls = Calls({"/jobs": {"ok": True}})

        def explode(_text):
            raise RuntimeError("no such app")

        with swap(_call=calls):
            cloudlink.once(explode, jobs=[JOB])
        self.assertIn("no such app", [d for _p, d in calls.made if d][0]["text"])

    def test_a_silent_skill_still_says_something(self):
        calls = Calls({"/jobs": {"ok": True}})
        with swap(_call=calls):
            cloudlink.once(lambda _text: "   ", jobs=[JOB])
        self.assertEqual([d for _p, d in calls.made if d][0]["text"], "Done, sir.")

    def test_an_empty_job_is_skipped(self):
        with swap(_call=Calls({"/jobs": {"ok": True}})):
            self.assertEqual(cloudlink.once(lambda _t: "x", jobs=[{"id": "j", "text": "  "}]), 0)


class CleaningTests(unittest.TestCase):
    """The cloud's marker, and the model's habit of repeating it."""

    def test_a_plain_request_is_left_alone(self):
        self.assertEqual(cloudlink.cleaned("open my notes"), "open my notes")

    def test_the_marker_is_stripped(self):
        self.assertEqual(cloudlink.cleaned("LAPTOP: open my notes"), "open my notes")

    def test_a_repeated_marker_does_not_double_the_request(self):
        mangled = "what's my battery LAPTOP: what's my battery"
        self.assertEqual(cloudlink.cleaned(mangled), "what's my battery")

    def test_nothing_at_all_is_not_a_request(self):
        self.assertEqual(cloudlink.cleaned(None), "")


class AddressTests(unittest.TestCase):
    def test_it_says_what_is_missing_rather_than_failing_quietly(self):
        with swap(address=lambda: "", secret=lambda: "s"):
            self.assertIn("where the cloud", cloudlink.start(lambda _t: ""))
        with swap(address=lambda: "https://x.workers.dev", secret=lambda: ""):
            self.assertIn("shared secret", cloudlink.start(lambda _t: ""))

    def test_every_call_says_who_it_is(self):
        """Without a user agent Cloudflare's edge turns the Mac away with a 403 that looks exactly
        like a wrong password - an hour of chasing the wrong problem."""
        seen = []

        class Fake:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def read(self):
                return b"{}"

        import urllib.request
        saved = urllib.request.urlopen
        urllib.request.urlopen = lambda request, timeout=0: seen.append(request) or Fake()
        try:
            with swap(address=lambda: "https://x.workers.dev", secret=lambda: "s"):
                cloudlink.waiting()
                cloudlink.report({"id": "1", "from": "91"}, "done")
        finally:
            urllib.request.urlopen = saved
        self.assertTrue(all(r.get_header("User-agent") == cloudlink.AGENT for r in seen))
        self.assertEqual(seen[1].get_header("Content-type"), "application/json")

    def test_the_secret_is_carried_on_every_call(self):
        seen = []

        class Fake:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def read(self):
                return json.dumps({"jobs": []}).encode()

        def opener(request, timeout=0):
            seen.append(request.full_url)
            return Fake()

        import urllib.request
        saved = urllib.request.urlopen
        urllib.request.urlopen = opener
        try:
            with swap(address=lambda: "https://x.workers.dev", secret=lambda: "sh h"):
                cloudlink.waiting()
        finally:
            urllib.request.urlopen = saved
        self.assertIn("s=sh%20h", seen[0])      # spaces and the like survive the trip


class WatchTests(unittest.TestCase):
    def test_it_keeps_asking(self):
        turns = []
        with swap(_call=Calls({"/jobs": {"jobs": []}}), once=lambda ask, jobs=None: turns.append(1)):
            cloudlink.watch(lambda _t: "", gap=0, rounds=3, sleep=lambda _s: None)
        self.assertEqual(len(turns), 3)

    def test_asking_for_work_is_one_request_not_two(self):
        """A quiet Mac should cost a single small call every few seconds, nothing more."""
        calls = Calls({"/jobs": {"jobs": []}})
        with swap(_call=calls):
            cloudlink.watch(lambda _t: "", gap=0, rounds=5, sleep=lambda _s: None)
        self.assertEqual([path for path, _data in calls.made], ["/jobs"] * 5)

    def test_work_that_is_waiting_is_carried_out(self):
        done = []
        with swap(_call=Calls({"/jobs": {"jobs": [JOB]}}), once=lambda ask, jobs=None: done.extend(jobs)):
            cloudlink.watch(lambda _t: "", gap=0, rounds=1, sleep=lambda _s: None)
        self.assertEqual(done, [JOB])

    def test_it_backs_off_when_the_cloud_is_not_there(self):
        waits = []
        with swap(_call=Calls({"/jobs": None})):
            cloudlink.watch(lambda _t: "", gap=1.0, rounds=2, sleep=waits.append)
        self.assertEqual(waits, [cloudlink.QUIET, cloudlink.QUIET])

    def test_a_crash_does_not_end_the_watch(self):
        def explode(*_a, **_k):
            raise RuntimeError("boom")

        with swap(_call=explode):
            cloudlink.watch(lambda _t: "", gap=0, rounds=2, sleep=lambda _s: None)   # must not raise


if __name__ == "__main__":
    unittest.main()
