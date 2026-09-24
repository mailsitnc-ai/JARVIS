"""Autocorrect for the point-and-type keyboard: fix the finger's typos, leave real words alone."""
import unittest

from core.wordfix import COMMON, KEEP, correct, fix_text, known, neighbours


class NeighbourTests(unittest.TestCase):
    def test_keys_know_who_they_touch(self):
        near = neighbours()
        self.assertIn("r", near["t"])          # same row
        self.assertIn("g", near["t"])          # diagonally below
        self.assertNotIn("p", near["t"])       # other side of the keyboard
        self.assertEqual(near["a"] & {"q", "w", "s", "z"}, {"q", "w", "s", "z"})


class CorrectTests(unittest.TestCase):
    def assertFix(self, typo, expected):
        self.assertEqual(correct(typo), expected, f"{typo!r} should become {expected!r}")

    def test_a_neighbouring_key(self):
        self.assertFix("wjat", "what")         # h -> j
        self.assertFix("tge", "the")
        self.assertFix("nkw", "now")

    def test_a_missed_double_letter(self):
        """Resting on a key repeats it - miss the beat and you drop the second letter."""
        self.assertFix("thre", "three")        # the user's own example
        self.assertFix("helo", "hello")

    def test_a_letter_typed_twice(self):
        self.assertFix("schoool", "school")
        self.assertFix("okayy", "okay")

    def test_two_letters_the_wrong_way_round(self):
        self.assertFix("teh", "the")
        self.assertFix("tomorrwo", "tomorrow")

    def test_a_longer_word_two_edits_out(self):
        self.assertFix("tommorow", "tomorrow")
        self.assertFix("definately", "definitely")

    def test_real_words_are_never_touched(self):
        for word in ("the", "school", "python", "jarvis", "hi", "okay", "message"):
            self.assertIsNone(correct(word), word)

    def test_chat_shorthand_is_left_alone(self):
        for word in ("sry", "plz", "wanna", "tmrw", "idk", "bro"):
            self.assertIn(word, KEEP)
            self.assertIsNone(correct(word), word)

    def test_gibberish_is_left_alone_rather_than_guessed_at(self):
        for word in ("wrkr", "zxqwv", "asdfgh"):
            self.assertIsNone(correct(word), word)

    def test_short_words_and_non_words_are_skipped(self):
        self.assertIsNone(correct("ab"))
        self.assertIsNone(correct("12"))
        self.assertIsNone(correct(""))
        self.assertIsNone(correct(None))

    def test_capitals_are_kept(self):
        self.assertEqual(correct("Tge"), "The")
        self.assertEqual(correct("TGE"), "THE")

    def test_a_whole_sentence(self):
        self.assertEqual(fix_text("hey can we meet tomorrwo at schoool"),
                         "hey can we meet tomorrow at school")
        self.assertEqual(fix_text("see you at 5 pm"), "see you at 5 pm")


class DictionaryTests(unittest.TestCase):
    def test_common_words_are_always_known_even_without_a_system_word_list(self):
        for word in ("tuition", "homework", "tomorrow", "sir"):
            self.assertIn(word, COMMON)
            self.assertTrue(known(word))

    def test_it_does_not_crash_without_the_system_list(self):
        import core.wordfix as wf
        real, wf.SYSTEM_WORDS = wf.SYSTEM_WORDS, "/nonexistent/words"
        wf.dictionary.cache_clear()
        try:
            self.assertTrue(known("school"))          # the built-in list still works
            self.assertEqual(correct("schoool"), "school")
        finally:
            wf.SYSTEM_WORDS = real
            wf.dictionary.cache_clear()


if __name__ == "__main__":
    unittest.main()
