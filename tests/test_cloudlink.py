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


class AddressTests(unittest.TestCase):
    def test_it_says_what_is_missing_rather_than_failing_quietly(self):
        with swap(address=lambda: "", secret=lambda: "s"):
            self.assertIn("where the cloud", cloudlink.start(lambda _t: ""))
        with swap(address=lambda: "https://x.workers.dev", secret=lambda: ""):
            self.assertIn("shared secret", cloudlink.start(lambda _t: ""))

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
        with swap(once=lambda ask: turns.append(1) or 1):
            cloudlink.watch(lambda _t: "", gap=0, rounds=3, sleep=lambda _s: None)
        self.assertEqual(len(turns), 3)

    def test_it_backs_off_when_the_cloud_is_not_there(self):
        waits = []
        with swap(once=lambda ask: 0, reachable=lambda: False):
            cloudlink.watch(lambda _t: "", gap=1.0, rounds=2, sleep=waits.append)
        self.assertEqual(waits, [cloudlink.QUIET, cloudlink.QUIET])

    def test_a_crash_does_not_end_the_watch(self):
        def explode(_ask):
            raise RuntimeError("boom")

        with swap(once=explode):
            cloudlink.watch(lambda _t: "", gap=0, rounds=2, sleep=lambda _s: None)   # must not raise


if __name__ == "__main__":
    unittest.main()
