"""Voice (wake word, speech cleanup, voice detection), heads-up alerts, briefing, and their routing."""
import datetime as dt
import unittest

from core.config import SKILLS_DIR
from core.skill_loader import SkillRegistry
from tests.helpers import IsolatedCase


class WakeWordTests(unittest.TestCase):
    def test_wake_and_command(self):
        from core.voice import parse_wake
        self.assertEqual(parse_wake("Jarvis, what time is it?"), (True, "what time is it?"))
        self.assertEqual(parse_wake("Hey Jarvis. Open Chrome."), (True, "Open Chrome"))
        self.assertEqual(parse_wake("OK Jarvis message Inaya saying hi"), (True, "message Inaya saying hi"))
        self.assertEqual(parse_wake("Jarvis."), (True, ""))                  # bare name -> "Yes, sir?"

    def test_not_addressed_to_jarvis_is_ignored(self):
        from core.voice import parse_wake
        for text in ("So I told him the movie was great.", "what's the weather like",
                     "I watched Jarvis in Iron Man", ""):
            self.assertFalse(parse_wake(text)[0], text)

    def test_extra_wake_words(self):
        from core.voice import parse_wake
        self.assertEqual(parse_wake("Friday, lights", extra_words=("friday",)), (True, "lights"))


class SpeakableTests(unittest.TestCase):
    def test_strips_links_paths_and_markdown(self):
        from core.voice import speakable
        out = speakable("Created the Google Doc 'Oct': https://docs.google.com/document/d/abc/edit")
        self.assertNotIn("http", out)
        self.assertIn("link in the panel", out)
        self.assertEqual(speakable("Wrote /Users/shivam/Desktop/primes.py for you"), "Wrote primes.py for you")
        self.assertNotIn("*", speakable("**Done**"))

    def test_long_replies_are_cut_to_a_sentence_or_two(self):
        from core.voice import speakable
        text = "This is a sentence about something. " * 30
        out = speakable(text)
        self.assertLess(len(out), 400)
        self.assertTrue(out.endswith("The rest is in the panel."))


class SegmenterTests(unittest.TestCase):
    def test_finds_an_utterance_between_silences(self):
        import numpy as np
        from core.voice import BLOCK, RATE, Segmenter
        rng = np.random.default_rng(1)
        quiet = (rng.standard_normal(RATE) * 0.002).astype(np.float32)
        t = np.arange(int(RATE * 1.2)) / RATE
        speech = (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        stream = np.concatenate([quiet, speech, quiet, quiet])
        seg, got = Segmenter(), []
        for i in range(0, len(stream) - BLOCK, BLOCK):
            a = seg.feed(stream[i:i + BLOCK])
            if a is not None:
                got.append(len(a) / RATE)
        self.assertEqual(len(got), 1)
        self.assertGreater(got[0], 1.1)

    def test_silence_and_noise_make_nothing(self):
        import numpy as np
        from core.voice import BLOCK, RATE, Segmenter
        rng = np.random.default_rng(2)
        stream = (rng.standard_normal(RATE * 5) * 0.003).astype(np.float32)
        seg = Segmenter()
        self.assertFalse(any(seg.feed(stream[i:i + BLOCK]) is not None
                             for i in range(0, len(stream) - BLOCK, BLOCK)))


class SentinelTests(unittest.TestCase):
    def test_battery_warns_once_per_level_and_resets_when_plugged(self):
        from core.sentinel import Sentinel
        s = Sentinel()
        self.assertEqual(s.check((50, False), 100, 0), [])
        self.assertEqual(len(s.check((19, False), 100, 60)), 1)
        self.assertEqual(s.check((18, False), 100, 120), [])          # same level: no repeat
        self.assertIn("10 percent", s.check((10, False), 100, 180)[0])
        self.assertIn("critical", s.check((4, False), 100, 240)[0])
        s.check((30, True), 100, 300)                                 # plugged in -> reset
        self.assertEqual(len(s.check((19, False), 100, 360)), 1)

    def test_jumping_straight_to_low_battery_gives_one_alert(self):
        from core.sentinel import Sentinel
        self.assertEqual(len(Sentinel().check((8, False), 100, 0)), 1)

    def test_disk_warns_at_most_daily(self):
        from core.sentinel import Sentinel
        s = Sentinel()
        self.assertEqual(len(s.check(None, 3.2, 100000)), 1)
        self.assertEqual(s.check(None, 3.0, 100000 + 3600), [])
        self.assertEqual(len(s.check(None, 3.0, 100000 + 90000)), 1)


class BriefingTests(unittest.TestCase):
    def test_greeting_and_time(self):
        from core.briefing import greeting, time_line
        self.assertEqual(greeting(dt.datetime(2026, 9, 23, 7, 5)), "Good morning")
        self.assertEqual(greeting(dt.datetime(2026, 9, 23, 14, 0)), "Good afternoon")
        self.assertEqual(greeting(dt.datetime(2026, 9, 23, 21, 0)), "Good evening")
        self.assertEqual(time_line(dt.datetime(2026, 9, 23, 7, 5)), "It's 7:05 AM on Wednesday the 23rd.")
        self.assertIn("the 11th", time_line(dt.datetime(2026, 9, 11, 9, 0)))

    def test_compose_survives_every_part_failing(self):
        from unittest import mock

        from core import briefing
        with mock.patch.object(briefing, "weather_line", side_effect=OSError), \
                mock.patch.object(briefing, "email_line", return_value=None), \
                mock.patch.object(briefing, "battery_line", return_value=None):
            out = briefing.compose(_Settings(), dt.datetime(2026, 9, 23, 7, 5))
        self.assertTrue(out.startswith("Good morning, sir. It's 7:05 AM"))


class _Settings:
    def get(self, key, default=None):
        return default


class RoutingTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.reg = SkillRegistry(SKILLS_DIR)
        self.reg.reload()

    def names(self, text):
        return {s.name for s, _ in self.reg.match(text)}

    def test_new_skills_route(self):
        self.assertIn("briefing", self.names("good morning jarvis"))
        self.assertIn("briefing", self.names("brief me"))
        self.assertIn("briefing", self.names("what's the weather like"))
        self.assertIn("voice_control", self.names("start listening"))
        self.assertIn("voice_control", self.names("stop listening"))

    def test_they_dont_hijack_messages(self):
        self.assertNotIn("briefing", self.names("whatsapp mom saying good morning"))
        self.assertNotIn("voice_control", self.names("send a whatsapp to Inaya saying stop listening to him"))


if __name__ == "__main__":
    unittest.main()
