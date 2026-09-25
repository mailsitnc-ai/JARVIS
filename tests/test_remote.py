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
        self.dead = False            # Chrome quit / crashed
        self.launched = 0            # how many times JARVIS started it again

    # -- the bits PhoneChannel uses --
    def whatsapp_open(self, chat):
        if self.dead:
            raise RuntimeError("lost the connection to Chrome")
        self.opened += 1
        return self.title

    def whatsapp_open_chat_title(self):
        if self.dead:
            raise RuntimeError("lost the connection to Chrome")
        return self.title

    def whatsapp_messages(self, limit=12):
        if self.dead:
            raise RuntimeError("lost the connection to Chrome")
        with self.lock:
            return [dict(row) for row in self.rows[-limit:]]

    def whatsapp_type_send(self, message):
        if self.dead:
            return "couldn't find the message box."
        with self.lock:
            self.sent.append(message)
            self.rows.append({"id": f"s{len(self.rows)}", "out": True, "audio": False,
                              "text": message, "meta": ""})
        return None

    def whatsapp_attach(self, path, caption=""):
        self.attached.append(path)
        return None

    # -- what a dead Chrome looks like --
    def alive(self):
        return not self.dead

    def ensure(self):
        if self.dead:
            self.launched += 1
            self.dead = False        # JARVIS started Chrome again

    def close(self):
        pass

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
        self.channel.SNAPSHOT_WAIT = 0.4
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


class BacklogTests(unittest.TestCase):
    """The bug from the first live run: starting up answered a message sent an hour earlier."""

    def test_the_timestamp_on_a_bubble_is_read(self):
        from core.remote import message_time
        when = message_time("[1:57 pm, 25/9/2026] Shivam G: ")
        self.assertEqual((when.hour, when.minute, when.day, when.month), (13, 57, 25, 9))
        self.assertEqual(message_time("[11:05 am, 1/1/2026] x:").hour, 11)
        self.assertEqual(message_time("[12:30 am, 1/1/2026] x:").hour, 0)      # midnight, not noon
        self.assertEqual(message_time("[12:30 pm, 1/1/2026] x:").hour, 12)
        self.assertIsNone(message_time("something else"))
        self.assertIsNone(message_time(""))

    def test_a_message_from_before_we_started_is_not_run(self):
        import datetime as dt
        fake = FakeWhatsApp()
        channel = PhoneChannel(lambda text: "ran it", "Message yourself", controller=fake,
                               voice=False, poll=0.05)
        channel.SNAPSHOT_WAIT = 0.4
        self.addCleanup(channel.stop)
        channel.start()
        yesterday = dt.datetime.now() - dt.timedelta(days=1)
        fake.rows.append({"id": "old1", "out": True, "audio": False, "text": "jarvis delete everything",
                          "meta": yesterday.strftime("[%I:%M %p, %-d/%-m/%Y] Shivam G:").lower()})
        time.sleep(0.4)
        self.assertEqual(fake.sent, [])

    def test_the_chat_is_read_only_once_whatsapp_has_drawn_it(self):
        """The real failure: the page was still loading, so nothing was marked as already-seen."""
        fake = FakeWhatsApp(history=["jarvis an old request"])
        empty_reads = [0]
        real_messages = fake.whatsapp_messages

        def slow(limit=12):
            empty_reads[0] += 1
            return [] if empty_reads[0] < 3 else real_messages(limit)

        fake.whatsapp_messages = slow
        channel = PhoneChannel(lambda text: "ran it", "Message yourself", controller=fake,
                               voice=False, poll=0.05)
        channel.SNAPSHOT_WAIT = 4.0
        self.addCleanup(channel.stop)
        channel.start()
        time.sleep(0.5)
        self.assertEqual(fake.sent, [])            # it waited, saw the history, and left it alone


class FollowUpTests(unittest.TestCase):
    """From the live log: JARVIS asked "what should the message say?" and the answer was ignored
    because it didn't start with "jarvis"."""

    def setUp(self):
        self.fake = FakeWhatsApp()
        self.asked = []
        self.replies = iter(["What would you like it to say?", "Sent it."])
        self.channel = PhoneChannel(self.answer, "Message yourself", controller=self.fake,
                                    voice=False, poll=0.05)
        self.channel.SNAPSHOT_WAIT = 0.4
        self.addCleanup(self.channel.stop)

    def answer(self, text):
        self.asked.append(text)
        return next(self.replies, "done")

    def test_answering_a_question_needs_no_wake_word(self):
        self.channel.start()
        self.fake.phone_says("jarvis whatsapp om")
        self.assertTrue(wait_for(lambda: any("like it to say" in m for m in self.fake.sent)))
        self.fake.phone_says("can we hoop today")          # no "jarvis" - it's an answer
        self.assertTrue(wait_for(lambda: len(self.asked) > 1))
        self.assertEqual(self.asked[1], "can we hoop today")

    def test_the_message_after_that_needs_the_wake_word_again(self):
        self.channel.start()
        self.fake.phone_says("jarvis whatsapp om")
        self.assertTrue(wait_for(lambda: self.fake.sent))
        self.fake.phone_says("can we hoop today")
        self.assertTrue(wait_for(lambda: len(self.asked) > 1))
        self.fake.phone_says("note to self: buy milk")     # not an answer, not addressed to JARVIS
        time.sleep(0.3)
        self.assertEqual(len(self.asked), 2)


class OwnWordsTests(unittest.TestCase):
    """WhatsApp renders our robot-face marker as an IMAGE, so the text alone can't prove it's ours."""

    def test_the_marker_survives_being_turned_into_a_picture(self):
        fake = FakeWhatsApp()
        channel = PhoneChannel(lambda text: "reply", "Message yourself", controller=fake,
                               voice=False, poll=0.05)
        channel.say("Jarvis here, battery is 94%")
        stripped = " Jarvis here, battery is 94%"      # what the page gives back, emoji gone
        self.assertTrue(channel._is_ours(stripped))
        self.assertFalse(channel._is_ours("jarvis what's my battery"))

    def test_close_enough_counts(self):
        from core.remote import same_words
        self.assertTrue(same_words("\U0001f916 Battery: 94%.", "Battery: 94%"))
        self.assertFalse(same_words("Battery: 94%", "Battery: 12%"))
        self.assertFalse(same_words("", ""))


class ConversationTests(unittest.TestCase):
    """Summon him once and just talk: "Hey Jarvis" opens a conversation, "ok dismissed" ends it."""

    def setUp(self):
        self.fake = FakeWhatsApp()
        self.asked = []
        self.channel = PhoneChannel(lambda text: self.asked.append(text) or f"done: {text}",
                                    "Message yourself", controller=self.fake, voice=False, poll=0.05)
        self.channel.SNAPSHOT_WAIT = 0.4
        self.addCleanup(self.channel.stop)
        self.channel.start()

    def says(self, text):
        self.fake.phone_says(text)

    def test_his_name_on_its_own_is_answered_and_opens_the_conversation(self):
        self.says("Hey Jarvis")
        self.assertTrue(wait_for(lambda: self.fake.sent), "a bare summons got no answer at all")
        self.assertIn("At your service", self.fake.sent[-1])
        self.assertTrue(self.channel.session)

    def test_everything_after_the_summons_reaches_him(self):
        self.says("Hey Jarvis")
        self.assertTrue(wait_for(lambda: self.channel.session))
        self.says("what is the time")
        self.says("and what is my battery")
        self.assertTrue(wait_for(lambda: len(self.asked) >= 2))
        self.assertEqual(self.asked, ["what is the time", "and what is my battery"])

    def test_ok_dismissed_ends_it_and_notes_are_notes_again(self):
        self.says("Hey Jarvis")
        self.assertTrue(wait_for(lambda: self.channel.session))
        self.says("ok dismissed")
        self.assertTrue(wait_for(lambda: not self.channel.session))
        self.assertIn("Standing by", self.fake.sent[-1])
        self.says("remember to buy milk")
        time.sleep(0.3)
        self.assertEqual(self.asked, [])

    def test_the_ways_of_saying_dismissed(self):
        from core.remote import DISMISS
        for words in ("ok dismissed", "dismissed", "you're dismissed", "jarvis dismissed",
                      "that's all", "thanks that'll be all", "we're done", "bye jarvis", "goodbye",
                      "stand down"):
            self.assertTrue(DISMISS.match(words), words)
        for keep in ("dismiss the alarm at 6", "that's all the homework i have", "bye means goodbye",
                     "stand down the ladder from the loft"):
            self.assertIsNone(DISMISS.match(keep), keep)

    def test_addressing_him_by_name_also_opens_the_conversation(self):
        self.says("jarvis what is the time")
        self.assertTrue(wait_for(lambda: self.asked))
        self.assertTrue(self.channel.session)
        self.says("and the weather")
        self.assertTrue(wait_for(lambda: len(self.asked) >= 2))
        self.assertEqual(self.asked[1], "and the weather")

    def test_a_name_run_into_the_message_still_counts(self):
        """Typed on a phone when the wake word is compulsory: "JarvisCould you open docs"."""
        from core.remote import command_in
        self.assertEqual(command_in("JarvisCould you open Google docs"), "Could you open Google docs")
        self.assertEqual(command_in("Jarvis, could you open docs"), "could you open docs")

    def test_before_any_summons_notes_are_left_alone(self):
        self.says("milk, eggs, bread")
        time.sleep(0.3)
        self.assertEqual(self.asked, [])
        self.assertEqual(self.fake.sent, [])


class NotifyTests(unittest.TestCase):
    """Other parts of JARVIS (like the call page) can drop a line into your own chat."""

    def test_a_note_goes_into_the_chat(self):
        fake = FakeWhatsApp()
        channel = PhoneChannel(lambda text: "x", "Message yourself", controller=fake, voice=False,
                               poll=0.05)
        channel.SNAPSHOT_WAIT = 0.4
        self.addCleanup(channel.stop)
        channel.start()
        remote._ACTIVE = channel
        self.addCleanup(lambda: setattr(remote, "_ACTIVE", None))
        self.assertTrue(remote.notify("Call me from anywhere: https://example.test"))
        self.assertTrue(any("example.test" in m for m in fake.sent))

    def test_it_says_no_when_nothing_is_watching(self):
        remote._ACTIVE = None
        self.assertFalse(remote.notify("nobody to tell"))


class ChromeGoesAwayTests(unittest.TestCase):
    """You shouldn't have to keep Chrome running: if it's quit or crashed, JARVIS brings it back."""

    def setUp(self):
        self.fake = FakeWhatsApp()
        self.asked = []
        self.notes = []
        self.channel = PhoneChannel(lambda text: self.asked.append(text) or f"done: {text}",
                                    "Message yourself", controller=self.fake, voice=False,
                                    poll=0.05, emit=self.notes.append)
        self.channel.SNAPSHOT_WAIT = 0.4
        self.channel.REVIVE_EVERY = 0.2
        self.addCleanup(self.channel.stop)
        self.channel.start()

    def test_chrome_is_started_again_by_itself(self):
        self.fake.dead = True
        self.assertTrue(wait_for(lambda: self.fake.launched >= 1), "Chrome was never restarted")
        self.assertTrue(any("bringing it back" in n for n in self.notes))
        self.fake.phone_says("jarvis what is the time")
        self.assertTrue(wait_for(lambda: self.asked), "it never started watching again")
        self.assertTrue(any("back" in n for n in self.notes))

    def test_a_message_sent_while_chrome_was_down_is_still_answered(self):
        self.fake.dead = True
        self.fake.rows.append({"id": "while-down", "out": True, "audio": False,
                               "text": "jarvis what is the time", "meta": ""})
        self.assertTrue(wait_for(lambda: self.asked, timeout=6))
        self.assertEqual(self.asked, ["what is the time"])

    def test_an_answer_is_held_and_delivered_when_the_chat_comes_back(self):
        self.fake.phone_says("jarvis first question")
        self.assertTrue(wait_for(lambda: self.fake.sent))
        self.fake.sent.clear()
        self.fake.dead = True
        self.channel.say("here is your answer")           # can't get out right now
        self.assertIn("here is your answer", self.channel.outbox)
        self.assertTrue(wait_for(lambda: any("here is your answer" in m for m in self.fake.sent),
                                 timeout=6), "the held answer never arrived")
        self.assertEqual(self.channel.outbox, [])


class SafetyTests(unittest.TestCase):
    """Orders come from YOUR chat only, and a broken voice note must not break the channel."""

    def test_a_message_in_someone_elses_chat_is_never_a_command(self):
        fake = FakeWhatsApp()
        asked = []
        channel = PhoneChannel(lambda text: asked.append(text) or "ran", "Message yourself",
                               controller=fake, voice=False, poll=0.05)
        channel.SNAPSHOT_WAIT = 0.4
        self.addCleanup(channel.stop)
        channel.start()
        fake.title = "Om"                       # a skill went off to another conversation
        fake.phone_says("jarvis delete everything")
        time.sleep(0.4)
        self.assertEqual(asked, [])
        fake.title = "Message yourself"         # back home: normal service resumes
        fake.phone_says("jarvis what is the time")
        self.assertTrue(wait_for(lambda: asked))
        self.assertEqual(asked, ["what is the time"])

    def test_voice_notes_switch_themselves_off_if_they_keep_failing(self):
        fake = FakeWhatsApp()
        fake.whatsapp_attach = lambda path, caption="": "WhatsApp never showed the Send button"
        fake.whatsapp_reset = lambda chat=None: True
        notes = []
        channel = PhoneChannel(lambda text: "an answer", "Message yourself", controller=fake,
                               voice=True, poll=0.05, emit=notes.append)
        channel.SNAPSHOT_WAIT = 0.4
        self.addCleanup(channel.stop)
        channel.start()
        fake.phone_says("jarvis one")
        self.assertTrue(wait_for(lambda: channel.voice_fails >= 1))
        fake.phone_says("jarvis two")
        self.assertTrue(wait_for(lambda: not channel.voice))
        self.assertTrue(any("text only" in n for n in notes))
        self.assertGreaterEqual(len(fake.sent), 2)          # the text replies still went out

    def test_whatsapp_is_put_back_in_order_after_every_request(self):
        fake = FakeWhatsApp()
        resets = []
        fake.whatsapp_reset = lambda chat=None: resets.append(chat) or True
        channel = PhoneChannel(lambda text: "done", "Message yourself", controller=fake,
                               voice=False, poll=0.05)
        channel.SNAPSHOT_WAIT = 0.4
        self.addCleanup(channel.stop)
        channel.start()
        fake.phone_says("jarvis do a thing")
        self.assertTrue(wait_for(lambda: resets))
        self.assertEqual(resets[0], "Message yourself")


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
        channel.SNAPSHOT_WAIT = 0.4
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
        channel.SNAPSHOT_WAIT = 0.4
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
