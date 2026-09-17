"""Conversation focus + reference resolution: "open it" / "the photo" -> the concrete thing."""
import time
import unittest
from unittest import mock

from core.actions import ActionBroker
from core.focus import Focus
from core.permissions import PermissionRegistry
from core.refs import resolve_references
from tests.helpers import IsolatedCase


class FocusStoreTests(IsolatedCase):
    def test_remember_and_get_roundtrip_survives_a_new_instance(self):
        f = Focus(self.tmp)
        self.assertIsNone(f.get("url"))
        f.remember("url", "https://example.com")
        self.assertEqual(Focus(self.tmp).get("url"), "https://example.com")  # persisted to disk

    def test_missing_path_slots_do_not_resolve(self):
        f = Focus(self.tmp)
        f.remember("file", str(self.tmp / "gone.txt"))  # never created
        self.assertIsNone(f.get("file"))                # a file that isn't there -> no referent

    def test_image_also_becomes_the_current_file(self):
        img = self.tmp / "shot.png"
        img.write_bytes(b"png")
        f = Focus(self.tmp)
        f.remember("image", str(img))
        self.assertEqual(f.get("image"), str(img))
        self.assertEqual(f.get("file"), str(img))  # "open it" after a photo opens the photo

    def test_most_recent_is_the_newest_valid_referent(self):
        doc = self.tmp / "a.txt"
        doc.write_text("x", encoding="utf-8")
        f = Focus(self.tmp)
        f.remember("file", str(doc))
        time.sleep(0.01)
        f.remember("url", "https://later.example")
        self.assertEqual(f.most_recent(), "https://later.example")
        self.assertEqual(set(dict(f.recent())), {"file", "url"})


class ResolveReferenceTests(IsolatedCase):
    def _focus_with(self, **slots):
        f = Focus(self.tmp)
        for slot, value in slots.items():
            f.remember(slot, value)
            time.sleep(0.005)  # keep a stable recency order across slots
        return f

    def setUp(self):
        super().setUp()
        self.photo = self.tmp / "cam.png"
        self.photo.write_bytes(b"png")
        self.doc = self.tmp / "notes.txt"
        self.doc.write_text("hi", encoding="utf-8")

    def test_open_the_photo_becomes_the_path(self):
        f = self._focus_with(image=str(self.photo))
        for phrase in ("open the photo", "open the picture", "show me the picture", "read that screenshot"):
            out, changed = resolve_references(phrase, f)
            self.assertTrue(changed, phrase)
            self.assertIn(str(self.photo), out, phrase)

    def test_bare_pronoun_in_a_command_resolves_to_most_recent(self):
        f = self._focus_with(file=str(self.doc))
        for phrase in ("open it", "run it", "please open that", "email it to me"):
            out, changed = resolve_references(phrase, f)
            self.assertTrue(changed, phrase)
            self.assertIn(str(self.doc), out, phrase)

    def test_whats_in_it_resolves(self):
        f = self._focus_with(image=str(self.photo))
        out, changed = resolve_references("what's in it", f)
        self.assertTrue(changed)
        self.assertIn(str(self.photo), out)

    def test_creation_and_indefinite_are_left_alone(self):
        f = self._focus_with(image=str(self.photo))
        for phrase in ("take a photo", "take another picture", "make me a picture", "open notepad"):
            out, changed = resolve_references(phrase, f)
            self.assertFalse(changed, phrase)
            self.assertEqual(out, phrase)

    def test_dummy_it_in_a_question_is_not_rewritten(self):
        f = self._focus_with(file=str(self.doc))
        for phrase in ("is it done?", "it's fine", "what is it"):
            out, changed = resolve_references(phrase, f)
            self.assertFalse(changed, phrase)

    def test_no_referent_means_no_change(self):
        out, changed = resolve_references("open the photo", Focus(self.tmp))
        self.assertFalse(changed)
        self.assertEqual(out, "open the photo")

    def test_url_reference_resolves_without_quoting(self):
        f = self._focus_with(url="https://example.com/page")
        out, changed = resolve_references("open the link", f)
        self.assertTrue(changed)
        self.assertIn("https://example.com/page", out)
        self.assertNotIn('"', out)  # URLs aren't quoted

    def test_video_reference_and_trailing_relative_clause(self):
        vid = self.tmp / "clip.mp4"
        vid.write_bytes(b"x")
        f = self._focus_with(file=str(vid))
        for phrase in ("open the video", "play the recording", "open the video you just recorded",
                       "show me the clip i made"):
            out, changed = resolve_references(phrase, f)
            self.assertTrue(changed, phrase)
            self.assertIn(str(vid), out, phrase)
            self.assertNotIn("recorded", out.replace(str(vid), ""), phrase)  # clause was consumed

    def test_explicit_absolute_path_is_left_untouched(self):
        vid = self.tmp / "clip.mp4"
        vid.write_bytes(b"x")
        f = self._focus_with(file=str(vid))
        phrase = f"open the video in this file path : {vid}"
        out, changed = resolve_references(phrase, f)
        self.assertFalse(changed)          # the skill extracts the path itself; don't garble it
        self.assertEqual(out, phrase)


class BrokerRecordsFocusTests(IsolatedCase):
    def test_write_file_and_open_url_update_the_focus(self):
        perms = PermissionRegistry(self.tmp / "p.json")
        perms.set("write_files", "allow")
        perms.set("open", "allow")
        broker = ActionBroker(permissions=perms, state_dir=self.tmp, browser="default")
        target = self.tmp / "made.txt"
        broker.write_file(str(target), "hi")
        with mock.patch("core.actions.webbrowser.open"):
            broker.open_url("example.com")
        f = Focus(self.tmp)
        self.assertEqual(f.get("file"), str(target))
        self.assertEqual(f.get("url"), "https://example.com")
        self.assertEqual(f.most_recent(), "https://example.com")  # opened after writing


class RecordVideoSkillTests(IsolatedCase):
    def _skill(self):
        from core.config import SKILLS_DIR
        from core.skill_loader import SkillRegistry
        reg = SkillRegistry(SKILLS_DIR)
        reg.reload()
        return reg.get("record_video")

    def test_duration_and_destination_parsing(self):
        skill = self._skill()
        mod = skill.module
        self.assertEqual(mod._duration("record a 10 second video of me"), 10.0)
        self.assertEqual(mod._duration("take a 5s clip"), 5.0)
        self.assertEqual(mod._duration("record a video of me"), 5.0)  # default
        self.assertIn("Desktop", str(mod._destination("record a video and save it on my desktop")))
        self.assertTrue(str(mod._destination(r"record to C:\Users\me\clip.mp4")).endswith("clip.mp4"))
        self.assertIsNone(mod._destination("record a 3 second video of me"))

    def test_dry_run_reports_the_duration(self):
        skill = self._skill()
        self.assertEqual(skill.run("record a 10 second video of me", {"dry_run": True}),
                         "Would record a 10s webcam video.")


if __name__ == "__main__":
    unittest.main()
