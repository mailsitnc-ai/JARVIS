"""WhatsApp / Google Chat sending: SENSITIVE gating, skill parsing, and the WhatsApp deep-link path."""
import unittest
from unittest import mock

from core.actions import ActionBroker, Blocked
from core.config import SKILLS_DIR
from core.permissions import PermissionRegistry
from core.skill_loader import SkillRegistry
from tests.helpers import IsolatedCase


def _skill(name):
    reg = SkillRegistry(SKILLS_DIR)
    reg.reload()
    return reg.get(name)


class MessageGatingTests(IsolatedCase):
    def test_message_send_is_sensitive_even_under_autonomy(self):
        perms = PermissionRegistry(self.tmp / "p.json")
        perms.set_autonomy(True)
        self.assertEqual(perms.state("browser"), "allow")     # normal cap: unleashed
        self.assertEqual(perms.state("message_send"), "ask")  # sending a message still always asks

    def test_dry_run_does_not_send(self):
        self.assertIn("Would send a WhatsApp message to +100",
                      ActionBroker(dry_run=True).send_message("whatsapp", "+100", "hi"))

    def test_denied_send_raises(self):
        perms = PermissionRegistry(self.tmp / "p.json")
        perms.set("message_send", "deny")
        with self.assertRaises(Blocked):
            ActionBroker(permissions=perms).send_message("whatsapp", "+100", "hi")

    def test_confirmer_sees_app_recipient_and_message(self):
        seen = []
        broker = ActionBroker(permissions=PermissionRegistry(self.tmp / "p.json"),
                              confirm=lambda req: seen.append(req) or "deny")
        with self.assertRaises(Blocked):
            broker.send_message("chat", "Priya", "standup at 10")
        self.assertEqual(seen[0].capability, "message_send")
        self.assertIn("Google Chat", seen[0].summary)
        self.assertIn("standup at 10", seen[0].details)


class StubActions:
    def __init__(self):
        self.sent = None

    def send_message(self, app, to, message):
        self.sent = {"app": app, "to": to, "message": message}
        return f"Sent to {to}."


class ParseTests(IsolatedCase):
    def test_whatsapp_name_and_message(self):
        act = StubActions()
        _skill("whatsapp").run("whatsapp mom saying I'll be late", {"actions": act})
        self.assertEqual(act.sent, {"app": "whatsapp", "to": "mom", "message": "I'll be late"})

    def test_whatsapp_phone_is_normalised(self):
        act = StubActions()
        _skill("whatsapp").run("send a whatsapp to +1 415 555 1234 saying hi", {"actions": act})
        self.assertEqual(act.sent["to"], "+14155551234")
        self.assertEqual(act.sent["message"], "hi")

    def test_whatsapp_colon_form(self):
        act = StubActions()
        _skill("whatsapp").run("message John on whatsapp: running late", {"actions": act})
        self.assertEqual(act.sent["to"], "John")
        self.assertEqual(act.sent["message"], "running late")

    def test_google_chat_parse(self):
        act = StubActions()
        _skill("google_chat").run("google chat Priya saying standup at 10", {"actions": act})
        self.assertEqual(act.sent, {"app": "chat", "to": "Priya", "message": "standup at 10"})

    def test_missing_message_asks(self):
        out = _skill("whatsapp").run("whatsapp Alex", {"actions": StubActions()})
        self.assertIn("What should I say", out)


class WhatsAppDeepLinkTests(unittest.TestCase):
    def test_phone_uses_send_deeplink_and_clicks_send(self):
        from core.browser import ChromeController
        c = ChromeController()
        c.ensure = lambda: None
        navigated = {}
        c.navigate = lambda url, wait=15: navigated.update(url=url)
        c._wait_for = lambda expr, timeout=25: True
        c.evaluate = lambda expr: True  # send button present + click succeeds
        out = c.whatsapp_send("+14155551234", "hello there")
        self.assertIn("web.whatsapp.com/send?phone=14155551234", navigated["url"])
        self.assertIn("hello%20there", navigated["url"])
        self.assertIn("Sent the WhatsApp message", out)


if __name__ == "__main__":
    unittest.main()
