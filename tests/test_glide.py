"""Swipe typing: turning a traced path into the word the user meant."""
import math
import random
import unittest

from core import glide
from core.gestures import Keyboard

ROWS = Keyboard.layout()
POINTS = glide.centres(ROWS, Keyboard.AREA)
WIDE = glide.key_size(ROWS, Keyboard.AREA)[0]


def swipe(word, noise=0.35, seed=1, rounded=True):
    """A hand-drawn version of a word: through the keys, jittery, with the corners rounded off
    (a real finger sails past a letter rather than stopping on it)."""
    rng = random.Random(seed)
    path, keys = [], [POINTS[c] for c in word]
    for a, b in zip(keys, keys[1:]):
        steps = max(3, int(math.dist(a, b) / (WIDE * 0.35)))
        for i in range(steps):
            f = i / steps
            path.append((a[0] + (b[0] - a[0]) * f + rng.gauss(0, noise * WIDE * 0.35),
                         a[1] + (b[1] - a[1]) * f + rng.gauss(0, noise * WIDE * 0.35)))
    path.append(keys[-1])
    if not rounded:
        return path
    out = []
    for i in range(len(path)):
        chunk = path[max(0, i - 3):i + 4]
        out.append((sum(p[0] for p in chunk) / len(chunk), sum(p[1] for p in chunk) / len(chunk)))
    return out


class GeometryTests(unittest.TestCase):
    def test_every_letter_has_a_centre_inside_the_keyboard(self):
        x0, y0, x1, y1 = Keyboard.AREA
        for key in "qwertyuiopasdfghjklzxcvbnm":
            x, y = POINTS[key]
            self.assertTrue(x0 < x < x1 and y0 < y < y1, key)
        self.assertLess(POINTS["q"][0], POINTS["p"][0])         # q is left of p
        self.assertLess(POINTS["q"][1], POINTS["z"][1])         # and above z

    def test_resampling_spreads_points_evenly_along_the_path(self):
        pts = glide.resample([(0, 0), (1, 0)], n=5)
        self.assertEqual(len(pts), 5)
        self.assertAlmostEqual(pts[2][0], 0.5, places=6)
        gaps = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
        self.assertAlmostEqual(min(gaps), max(gaps), places=6)

    def test_resampling_survives_a_hand_that_did_not_move(self):
        self.assertEqual(len(glide.resample([(0.5, 0.5)] * 9, n=6)), 6)

    def test_shape_ignores_size_and_position(self):
        small = glide.shape_of(glide.resample([(0.0, 0.0), (0.1, 0.1), (0.2, 0.0)]))
        big = glide.shape_of(glide.resample([(0.5, 0.5), (0.9, 0.9), (1.3, 0.5)]))
        for a, b in zip(small, big):
            self.assertAlmostEqual(a[0], b[0], places=6)
            self.assertAlmostEqual(a[1], b[1], places=6)

    def test_a_words_ideal_path_runs_through_its_keys(self):
        path = glide.ideal_path("hi", POINTS)
        self.assertEqual(path, [POINTS["h"], POINTS["i"]])
        self.assertEqual(glide.ideal_path("all", POINTS), [POINTS["a"], POINTS["l"]])   # no repeats
        self.assertIsNone(glide.ideal_path("a1", POINTS))


class DrawingMatchesAimingTests(unittest.TestCase):
    """Where a key is DRAWN on screen has to match where you have to point for it."""

    def test_the_suggestion_strip_is_drawn_the_size_it_is_aimed_at(self):
        from core import overlay
        x0, y0, x1, y1 = Keyboard.AREA
        aimed = Keyboard.SUGGEST / ((y1 - y0) + Keyboard.SUGGEST)
        self.assertAlmostEqual(overlay.STRIP, aimed, places=3)

    def test_a_suggestion_cell_is_where_the_strip_says_it_is(self):
        kb = Keyboard()
        kb.suggestions = ["one", "two", "three"]
        x0, y0, x1, y1 = Keyboard.AREA
        middle_x = x0 + (x1 - x0) * 0.5
        self.assertEqual(kb.key_at(middle_x, y0 - Keyboard.SUGGEST / 2), "sug1")
        self.assertEqual(kb.key_at(x0 + (x1 - x0) * 0.1, y0 - Keyboard.SUGGEST / 2), "sug0")
        self.assertEqual(kb.key_at(middle_x, y0 - Keyboard.SUGGEST * 2), None)   # above the strip
        kb.suggestions = []
        self.assertIsNone(kb.key_at(middle_x, y0 - Keyboard.SUGGEST / 2))        # no strip, no cells


class DecodeTests(unittest.TestCase):
    def setUp(self):
        self.vocab = glide.Vocabulary()

    def decode(self, path):
        return glide.decode(path, POINTS, WIDE, self.vocab, deep=glide.dictionary_words)

    def test_everyday_words(self):
        for word in ("hello", "school", "tomorrow", "what", "doing", "sorry", "homework",
                     "message", "please", "today", "laptop", "jarvis", "tuition"):
            got, _ = self.decode(swipe(word, seed=hash(word) % 1000))
            self.assertEqual(got[:1], [word], f"swiped {word!r}, got {got}")

    def test_words_that_trace_the_same_path_are_both_offered(self):
        """'three' and 'there' run through almost the same keys - a phone gets this wrong too, so
        both are offered and you can point at the other one instead of deleting."""
        got, _ = self.decode(swipe("three", seed=9))
        self.assertIn("three", got)
        self.assertIn("there", got)

    def test_a_sloppy_swipe_still_lands(self):
        got, _ = self.decode(swipe("tomorrow", noise=0.8, seed=11))
        self.assertEqual(got[0], "tomorrow")

    def test_alternatives_are_offered_not_just_the_winner(self):
        got, _ = self.decode(swipe("there", seed=5))
        self.assertGreater(len(got), 1)
        self.assertIn("there", got)

    def test_a_scribble_types_nothing_rather_than_guessing(self):
        rng = random.Random(3)
        scribble = [(0.2 + rng.random() * 0.6, 0.35 + rng.random() * 0.6) for _ in range(30)]
        got, cost = self.decode(scribble)
        self.assertEqual(got, [])
        self.assertGreater(cost, glide.MAX)

    def test_a_word_you_typed_by_hand_can_be_swiped_afterwards(self):
        made_up = "shivam"
        self.assertNotIn(made_up, self.vocab.words)
        self.vocab.learn(made_up)
        self.assertIn(made_up, self.vocab.words)
        got, _ = self.decode(swipe(made_up, seed=2))
        self.assertEqual(got[0], made_up)

    def test_learning_ignores_rubbish(self):
        self.vocab.learn("  ")
        self.vocab.learn("a")
        self.vocab.learn("12ab")
        self.assertEqual(self.vocab.learned, {})

    def test_learned_words_are_remembered_between_sessions(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "typed_words.json"
            first = glide.Vocabulary(store)
            first.learn("mun")
            first.learn("mun")
            self.assertEqual(json.loads(store.read_text()), {"mun": 2})
            self.assertIn("mun", glide.Vocabulary(store).words)

    def test_a_broken_store_does_not_break_typing(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "typed_words.json"
            store.write_text("{not json")
            vocab = glide.Vocabulary(store)
            self.assertEqual(vocab.learned, {})
            self.assertIn("hello", vocab.words)


if __name__ == "__main__":
    unittest.main()
