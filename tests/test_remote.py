"""JARVIS on your phone: reading the WhatsApp chat, running what you asked, answering back."""
import threading
import time
import unittest

from core import remote
from core.remote import MARK, PhoneChannel, command_in, decision


class FakeWhatsApp:
    """A WhatsApp Web that lives in memory: messages in a list, replies appended to it."""

    def __init__(self, history=()):
        self.rows = [{"id": f"h{i}", "out": True, "audio": False, "text": t, "meta": ""}
                     for i, t in enumerate(history)]
        self.sent = []
        self.attached = []
        self.title = "Message yourself"
        self.lock = threading.Lock()
        self.opened = 0

    # -- the bits PhoneChannel uses --
    def whatsapp_open(self, chat):
        self.opened += 1
        return self.title

    def whatsapp_open_chat_title(self):
        return self.title

    def whatsapp_messages(self, limit=12):
        with self.lock:
            return [dict(row) for row in self.rows[-limit:]]

    def whatsapp_type_send(self, message):
        with self.lock:
            self.sent.append(message)
            self.rows.append({"id": f"s{len(self.rows)}", "out": True, "audio": False,
                              "text": message, "meta": ""})
        return None

    def whatsapp_attach(self, path, caption=""):
        self.attached.append(path)
        return None

    # -- the test's side --
    def phone_says(self, text, audio=False):
        with self.lock:
            self.rows.append({"id": f"p{len(self.rows)}", "out": True, "audio": audio,
                              "text": text, "meta": "[11:00, 25/09/2026] You:"})


def wait_for(check, timeout=4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False


class MessageReadingTests(unittest.TestCase):
    def test_a_command_needs_jarvis_in_front_by_default(self):
        self.assertEqual(command_in("Jarvis, what's my battery?"), "what's my battery?")
        self.assertEqual(command_in("jarvis summarise it"), "summarise it")
        self.assertEqual(command_in("hey jarvis open docs"), "open docs")
        self.assertEqual(command_in("/briefing"), "briefing")
        self.assertIsNone(command_in("milk, eggs, bread"))       # a note to yourself stays a note
        self.assertIsNone(command_in("   "))

    def test_without_the_wake_word_everything_is_a_command(self):
        self.assertEqual(command_in("what's my battery?", require_wake=False), "what's my battery?")
        self.assertEqual(command_in("jarvis do it", require_wake=False), "do it")

    def test_jarvis_never_reads_its_own_replies(self):
        reply = f"{MARK} Jarvis, here's your battery: 80%"
        self.assertIsNone(command_in(reply))
        self.assertIsNone(command_in(reply, require_wake=False))

    def test_yes_and_no(self):
        for yes in ("yes", "Y", "yeah go ahead", "ok", "do it", "confirm"):
            self.assertEqual(decision(yes), "once", yes)
        for no in ("no", "nope", "stop", "don't", "cancel", "never mind"):
            self.assertEqual(decision(no), "deny", no)
        self.assertEqual(decision("yes, always"), "always")
        self.assertIsNone(decision("what's the weather"))        # not an answer at all


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.asked = []
        self.fake = FakeWhatsApp(history=["jarvis this is old and must not run"])
        self.channel = PhoneChannel(self.answer, "Message yourself", controller=self.fake,
                                    voice=False, poll=0.05)
        self.addCleanup(self.channel.stop)

    def answer(self, text):
        self.asked.append(text)
        return f"done: {text}"

    def start(self):
        self.assertIn("Watching WhatsApp", self.channel.start())

    def test_history_is_not_a_backlog_of_orders(self):
        self.start()
        time.sleep(0.3)
        self.assertEqual(self.asked, [])                         # the old message is left alone

    def test_a_message_from_the_phone_is_run_and_answered(self):
        self.start()
        self.fake.phone_says("Jarvis, what's my battery?")
        self.assertTrue(wait_for(lambda: self.fake.sent), "nothing was sent back")
        self.assertEqual(self.asked, ["what's my battery?"])
        self.assertTrue(self.fake.sent[0].startswith(MARK))      # marked, so we can't answer ourselves
        self.assertIn("done: what's my battery?", self.fake.sent[0])

    def test_notes_to_yourself_are_ignored(self):
        self.start()
        self.fake.phone_says("remember to buy milk")
        time.sleep(0.3)
        self.assertEqual(self.asked, [])
        self.assertEqual(self.fake.sent, [])

    def test_its_own_reply_does_not_start_another_round(self):
        self.start()
        self.fake.phone_says("jarvis hello")
        self.assertTrue(wait_for(lambda: self.fake.sent))
        time.sleep(0.4)
        self.assertEqual(len(self.asked), 1)                     # not a loop

    def test_a_risky_command_asks_before_it_runs(self):
        self.start()
        self.fake.phone_says("jarvis delete report.pdf")
        self.assertTrue(wait_for(lambda: any("delete something" in m for m in self.fake.sent)),
                        "it never asked")
        self.assertEqual(self.asked, [])                         # nothing ran while it waited
        self.fake.phone_says("yes")
        self.assertTrue(wait_for(lambda: self.asked))
        self.assertEqual(self.asked, ["delete report.pdf"])

    def test_saying_no_means_it_never_runs(self):
        self.start()
        self.fake.phone_says("jarvis delete report.pdf")
        self.assertTrue(wait_for(lambda: any("delete something" in m for m in self.fake.sent)))
        self.fake.phone_says("no")
        self.assertTrue(wait_for(lambda: any("Left it alone" in m for m in self.fake.sent)))
        time.sleep(0.2)
        self.assertEqual(self.asked, [])

    def test_a_new_request_instead_of_an_answer_is_not_lost(self):
        self.start()
        self.fake.phone_says("jarvis delete report.pdf")
        self.assertTrue(wait_for(lambda: any("delete something" in m for m in self.fake.sent)))
        self.fake.phone_says("jarvis what's the time")           # not yes or no: a new request
        self.assertTrue(wait_for(lambda: "what's the time" in self.asked))
        self.assertNotIn("delete report.pdf", self.asked)        # the risky one was dropped, not run

    def test_a_skill_asking_mid_request_reaches_the_chat(self):
        """Nothing about the words was risky, but the skill itself needs permission."""
        decisions = []

        def ask(text):
            self.asked.append(text)
            request = type("Request", (), {"summary": "read your Gmail", "capability": "google"})()
            decisions.append(self.channel.confirm(request))
            return "Read them." if decisions[-1] != "deny" else "Left them."

        self.channel.ask = ask
        self.start()
        self.fake.phone_says("jarvis what's in my inbox")
        self.assertTrue(wait_for(lambda: any("May I" in m for m in self.fake.sent)), "never asked")
        self.fake.phone_says("yes")
        self.assertTrue(wait_for(lambda: decisions))
        self.assertEqual(decisions[0], "once")

    def test_the_reply_is_trimmed_rather_than_flooding_the_chat(self):
        self.channel.ask = lambda text: "x" * 9000
        self.start()
        self.fake.phone_says("jarvis long one")
        self.assertTrue(wait_for(lambda: self.fake.sent))
        self.assertLessEqual(len(self.fake.sent[0]), PhoneChannel.MAX_REPLY + 40)
        self.assertIn("trimmed", self.fake.sent[0])

    def test_a_failing_request_is_reported_not_swallowed(self):
        def boom(_text):
            raise RuntimeError("the skill exploded")

        self.channel.ask = boom
        self.start()
        self.fake.phone_says("jarvis break")
        self.assertTrue(wait_for(lambda: self.fake.sent))
        self.assertIn("the skill exploded", self.fake.sent[0])

    def test_stopping_really_stops(self):
        self.start()
        self.assertTrue(self.channel.running())
        self.channel.stop()
        self.assertTrue(wait_for(lambda: not self.channel.running()))
        self.fake.phone_says("jarvis hello")
        time.sleep(0.3)
        self.assertEqual(self.asked, [])


class RiskTests(unittest.TestCase):
    """Commands from the phone that can't be taken back are checked first, whatever autonomy says."""

    def test_the_risky_ones_are_spotted(self):
        from core.remote import risk
        for command, why in (("delete the physics pdf", "delete"),
                             ("send a whatsapp to mum saying i'm late", "message"),
                             ("post it on instagram", "publicly"),
                             ("buy the book on amazon", "money"),
                             ("shut down the mac", "shut"),
                             ("run the command rm -rf build", "command"),
                             ("sudo pip install requests", "command"),
                             ("change the setting for autonomy", "set up")):
            self.assertIsNotNone(risk(command), command)
            self.assertIn(why, risk(command), command)

    def test_ordinary_requests_are_not_treated_as_risky(self):
        from core.remote import risk
        for command in ("what's my battery", "summarise the physics pdf", "what's the weather",
                        "remind me at 6 to revise", "read me my emails", "send me the briefing",
                        "what's on my calendar tomorrow", "open google docs"):
            self.assertIsNone(risk(command), command)


class PreapprovalTests(unittest.TestCase):
    def test_saying_yes_once_is_not_asked_again_by_the_skill(self):
        fake = FakeWhatsApp()
        channel = PhoneChannel(lambda text: "gone", "Message yourself", controller=fake,
                               voice=False, poll=0.05)
        self.addCleanup(channel.stop)
        channel.preapproved = True
        request = type("Request", (), {"summary": "delete it", "capability": "write_files"})()
        self.assertEqual(channel.confirm(request), "once")
        self.assertEqual(fake.sent, [])            # nothing had to be asked


class VoiceNoteTests(unittest.TestCase):
    def test_a_voice_note_is_transcribed_and_then_run(self):
        fake = FakeWhatsApp()
        channel = PhoneChannel(lambda text: f"done: {text}", "Message yourself", controller=fake,
                               voice=False, poll=0.05)
        channel._transcribe = lambda message: "jarvis what's my battery"
        self.addCleanup(channel.stop)
        channel.start()
        fake.phone_says("", audio=True)
        self.assertTrue(wait_for(lambda: fake.sent))
        self.assertIn("done: what's my battery", fake.sent[-1])

    def test_an_unreadable_voice_note_says_so_instead_of_guessing(self):
        fake = FakeWhatsApp()
        channel = PhoneChannel(lambda text: "should not run", "Message yourself", controller=fake,
                               voice=False, poll=0.05)
        self.addCleanup(channel.stop)
        import core.voicenote as voicenote
        original = voicenote.transcribe
        voicenote.transcribe = lambda controller, name="base.en": ""
        self.addCleanup(lambda: setattr(voicenote, "transcribe", original))
        channel.start()
        fake.phone_says("", audio=True)
        self.assertTrue(wait_for(lambda: fake.sent))
        self.assertIn("couldn't hear that voice note", fake.sent[-1])


class SpeechFileTests(unittest.TestCase):
    def test_what_gets_spoken_is_cleaned_up_first(self):
        from core.speechfile import speakable
        spoken = speakable("- **Battery**: 80%\n- Path: /Users/me/x.pdf\n🔋 `code`")
        self.assertNotIn("*", spoken)
        self.assertNotIn("/Users/me", spoken)
        self.assertIn("Battery", spoken)

    def test_nothing_to_say_makes_no_file(self):
        from core.speechfile import to_audio
        self.assertIsNone(to_audio("   "))


class ModuleApiTests(unittest.TestCase):
    def test_it_asks_for_a_chat_before_it_can_start(self):
        remote.configure(lambda text: "", emit=None, settings=None)
        self.assertIn("Which chat", remote.start(None))

    def test_status_when_off(self):
        self.assertIn("off", remote.status().lower())


if __name__ == "__main__":
    unittest.main()
