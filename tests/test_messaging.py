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
        self.logged_in = False

    def send_message(self, app, to, message):
        self.sent = {"app": app, "to": to, "message": message}
        return f"Sent to {to}."

    def whatsapp_login(self):
        self.logged_in = True
        return "linking"


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
        self.assertIn("What should I send", out)

    def test_whatsapp_group_and_dash_body(self):
        act = StubActions()
        _skill("whatsapp").run(
            'send a whatsapp in the Maa Paa group - @all standup at 10, be there', {"actions": act})
        self.assertEqual(act.sent["to"], "Maa Paa")
        self.assertEqual(act.sent["message"], "@all standup at 10, be there")

    def test_whatsapp_the_x_group_form(self):
        act = StubActions()
        _skill("whatsapp").run("whatsapp the family group saying dinner's ready", {"actions": act})
        self.assertEqual(act.sent["to"], "family")
        self.assertEqual(act.sent["message"], "dinner's ready")

    def test_whatsapp_quoted_body(self):
        act = StubActions()
        _skill("whatsapp").run('text Priya "see you at 6"', {"actions": act})
        self.assertEqual(act.sent["to"], "Priya")
        self.assertEqual(act.sent["message"], "see you at 6")

    def test_google_chat_space(self):
        act = StubActions()
        _skill("google_chat").run("message the Design space on chat: shipping today", {"actions": act})
        self.assertEqual(act.sent["to"], "Design")
        self.assertEqual(act.sent["message"], "shipping today")

    def test_whatsapp_login_intent_routes_to_linking(self):
        for cmd in ("log in to whatsapp", "connect whatsapp", "scan the whatsapp qr", "set up whatsapp"):
            act = StubActions()
            out = _skill("whatsapp").run(cmd, {"actions": act})
            self.assertTrue(act.logged_in, cmd)
            self.assertIsNone(act.sent, cmd)  # a login must never send a message
            self.assertEqual(out, "linking", cmd)

    def test_message_mentioning_link_still_sends(self):
        # "link" in the body must NOT be read as a login request.
        act = StubActions()
        _skill("whatsapp").run("whatsapp mom saying send me the link", {"actions": act})
        self.assertEqual(act.sent, {"app": "whatsapp", "to": "mom", "message": "send me the link"})
        self.assertFalse(act.logged_in)

    def test_login_broker_is_browser_gated_dry_run(self):
        self.assertIn("Would open WhatsApp Web",
                      ActionBroker(dry_run=True).whatsapp_login())


class ParserUnitTests(unittest.TestCase):
    def test_group_in_phrasing(self):
        from core.messaging import parse_message_command
        to, is_group, msg = parse_message_command(
            "send this message via whatsapp in the Maa Paa group - hello everyone")
        self.assertEqual((to, is_group, msg), ("Maa Paa", True, "hello everyone"))

    def test_plain_name(self):
        from core.messaging import parse_message_command
        to, is_group, msg = parse_message_command("whatsapp mom saying I'll be late")
        self.assertEqual((to, is_group, msg), ("mom", False, "I'll be late"))

    def test_phone_kept_only_with_allow_phone(self):
        from core.messaging import parse_message_command
        self.assertEqual(parse_message_command("whatsapp +1 415 555 1234 saying hi")[0], "+14155551234")


class RoutingHazardTests(IsolatedCase):
    def test_speak_text_does_not_hijack_a_messaging_command(self):
        """The messaging command must NOT match speak_text (its old \\bsaying\\b trigger did)."""
        reg = SkillRegistry(SKILLS_DIR)
        reg.reload()
        names = {s.name for s, _ in reg.match("whatsapp the Maa Paa group saying @all hello")}
        self.assertIn("whatsapp", names)
        self.assertNotIn("speak_text", names)

    def test_speak_text_still_triggers_on_real_tts(self):
        reg = SkillRegistry(SKILLS_DIR)
        reg.reload()
        for cmd in ("say hello out loud", "speak this: meeting at noon", "saying good morning"):
            names = {s.name for s, _ in reg.match(cmd)}
            self.assertIn("speak_text", names, cmd)


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
