"""WhatsApp without a browser: Meta delivers, JARVIS answers, the reply goes back over the API."""
import json
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import wacloud  # noqa: E402


def webhook(text="what time is it", sender="919770094860", ident="wamid.1", kind="text"):
    message = {"id": ident, "from": sender, "type": kind}
    if kind == "text":
        message["text"] = {"body": text}
    return {"object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {"messages": [message]}}]}]}


class Channel(wacloud.CloudChannel):
    """A channel whose replies are collected instead of sent to Meta."""

    def __init__(self, reply="It's 16:40.", **kwargs):
        self.sent = []
        self.done = threading.Event()
        super().__init__(lambda text: reply, token="t", phone_id="1", send=self._collect, **kwargs)

    def _collect(self, to, body):
        self.sent.append((to, body))
        self.done.set()
        return True


class ReadingTests(unittest.TestCase):
    def test_a_message_is_found_in_metas_shape(self):
        found = wacloud.parse(webhook("open my notes"))
        self.assertEqual(found, [{"id": "wamid.1", "from": "919770094860", "text": "open my notes"}])

    def test_delivery_receipts_are_not_messages(self):
        statuses = {"entry": [{"changes": [{"value": {"statuses": [{"status": "delivered"}]}}]}]}
        self.assertEqual(wacloud.parse(statuses), [])

    def test_a_photo_with_no_words_is_left_alone(self):
        self.assertEqual(wacloud.parse(webhook(kind="image")), [])

    def test_nonsense_does_not_bring_it_down(self):
        for payload in ({}, {"entry": None}, {"entry": [{}]}, {"entry": [{"changes": [{}]}]}):
            self.assertEqual(wacloud.parse(payload), [])

    def test_a_number_is_the_same_however_it_is_written(self):
        self.assertEqual(wacloud.digits("+91 97700 94860"), "919770094860")


class AnsweringTests(unittest.TestCase):
    def test_a_message_is_answered_back_to_the_sender(self):
        channel = Channel(allowed=["+91 97700 94860"])
        self.assertEqual(channel.deliver(webhook()), 1)
        self.assertTrue(channel.done.wait(3))
        self.assertEqual(channel.sent, [("919770094860", "It's 16:40.")])

    def test_only_your_own_number_may_command_it(self):
        channel = Channel(allowed=["919770094860"])
        self.assertEqual(channel.deliver(webhook(sender="14155550000")), 0)
        self.assertEqual(channel.sent, [])

    def test_with_no_list_of_numbers_it_answers_anyone(self):
        self.assertEqual(Channel().deliver(webhook(sender="14155550000")), 1)

    def test_the_same_message_twice_is_carried_out_once(self):
        """Meta re-sends anything it isn't promptly told arrived - which must not mean doing it
        twice."""
        channel = Channel()
        self.assertEqual(channel.deliver(webhook(ident="wamid.9")), 1)
        self.assertEqual(channel.deliver(webhook(ident="wamid.9")), 0)

    def test_a_request_that_blows_up_still_gets_an_answer(self):
        channel = Channel()
        channel.ask = lambda _text: (_ for _ in ()).throw(RuntimeError("the skill fell over"))
        channel.deliver(webhook())
        self.assertTrue(channel.done.wait(3))
        self.assertIn("fell over", channel.sent[0][1])

    def test_an_empty_answer_still_says_something(self):
        channel = Channel(reply="   ")
        channel.deliver(webhook())
        self.assertTrue(channel.done.wait(3))
        self.assertEqual(channel.sent[0][1], "Done, sir.")

    def test_a_very_long_answer_is_cut_to_what_whatsapp_accepts(self):
        channel = Channel()
        channel.say("x" * 6000, to="919770094860")
        self.assertLessEqual(len(channel.sent[0][1]), wacloud.MAX_REPLY)


class HookupTests(unittest.TestCase):
    """Meta proves the address is yours before it will send anything."""

    def test_the_word_you_chose_is_echoed_back(self):
        channel = Channel(verify="open-sesame")
        self.assertEqual(channel.challenge({"hub.mode": "subscribe",
                                            "hub.verify_token": "open-sesame",
                                            "hub.challenge": "12345"}), (200, "12345"))

    def test_the_wrong_word_gets_nothing(self):
        channel = Channel(verify="open-sesame")
        self.assertEqual(channel.challenge({"hub.mode": "subscribe",
                                            "hub.verify_token": "guess", "hub.challenge": "1"})[0], 403)
        self.assertEqual(channel.challenge({})[0], 403)


class ServedTests(unittest.TestCase):
    """The webhook hangs off the same server as the call page, so one address does both."""

    def setUp(self):
        from core.talk import HOOKS, TalkServer
        self.hooks = HOOKS
        self.saved = dict(HOOKS)
        self.channel = Channel(verify="a-word")
        HOOKS["/wa"] = {"get": self.channel.challenge, "post": self.channel.deliver}
        self.server = TalkServer(lambda text: "hello", port=0, https=False, secret="s",
                                 transcribe=lambda raw: "", voice=False)
        self.server.start()
        self.addCleanup(self.server.stop)
        self.addCleanup(lambda: (HOOKS.clear(), HOOKS.update(self.saved)))
        self.base = f"http://127.0.0.1:{self.server.port}"

    def fetch(self, path, data=None):
        import urllib.error
        import urllib.request
        request = urllib.request.Request(self.base + path, data=data,
                                         headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(request, timeout=10) as answer:
                return answer.status, answer.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_meta_can_hook_up_through_the_same_address(self):
        status, body = self.fetch("/wa?k=s&hub.mode=subscribe&hub.verify_token=a-word&hub.challenge=77")
        self.assertEqual((status, body), (200, b"77"))

    def test_a_message_posted_there_is_answered(self):
        status, _ = self.fetch("/wa?k=s", data=json.dumps(webhook()).encode())
        self.assertEqual(status, 200)
        self.assertTrue(self.channel.done.wait(3))
        self.assertEqual(self.channel.sent[0][0], "919770094860")

    def test_meta_is_told_it_arrived_at_once_even_if_the_work_fails(self):
        self.hooks["/wa"]["post"] = lambda payload: (_ for _ in ()).throw(RuntimeError("nope"))
        self.assertEqual(self.fetch("/wa?k=s", data=b"{}")[0], 200)

    def test_rubbish_instead_of_json_is_turned_away(self):
        self.assertEqual(self.fetch("/wa?k=s", data=b"not json")[0], 400)


if __name__ == "__main__":
    unittest.main()
