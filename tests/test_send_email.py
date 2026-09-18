"""Sending email: SENSITIVE gating (always asks, even under autonomy) + compose/parse in the skill."""
import unittest

from core.actions import ActionBroker, Blocked
from core.config import SKILLS_DIR
from core.permissions import PermissionRegistry
from core.skill_loader import SkillRegistry
from tests.helpers import IsolatedCase


def _skill(name):
    reg = SkillRegistry(SKILLS_DIR)
    reg.reload()
    return reg.get(name)


class SendGatingTests(IsolatedCase):
    def test_email_send_is_sensitive_even_under_autonomy(self):
        perms = PermissionRegistry(self.tmp / "p.json")
        perms.set_autonomy(True)
        self.assertEqual(perms.state("open"), "allow")        # normal cap: unleashed
        self.assertEqual(perms.state("email_send"), "ask")    # sending still asks, always

    def test_dry_run_does_not_send(self):
        self.assertIn("Would send an email to bob@x.com", ActionBroker(dry_run=True).gmail_send("bob@x.com", "Hi", "yo"))

    def test_denied_send_raises(self):
        perms = PermissionRegistry(self.tmp / "p.json")
        perms.set("email_send", "deny")
        with self.assertRaises(Blocked):
            ActionBroker(permissions=perms).gmail_send("bob@x.com", "Hi", "body")

    def test_confirmer_sees_recipient_and_body(self):
        seen = []
        perms = PermissionRegistry(self.tmp / "p.json")
        broker = ActionBroker(permissions=perms, confirm=lambda req: seen.append(req) or "deny")
        with self.assertRaises(Blocked):
            broker.gmail_send("bob@x.com", "Lunch", "Are we still on for lunch?")
        self.assertEqual(seen[0].capability, "email_send")
        self.assertIn("bob@x.com", seen[0].summary)
        self.assertIn("Are we still on for lunch?", seen[0].details)  # full body shown before approving


class StubLLM:
    def __init__(self, replies):
        self.replies = list(replies)

    def __call__(self, prompt, system=None, temperature=0, max_tokens=0):
        return self.replies.pop(0) if self.replies else "generated body"


class StubActions:
    def __init__(self, address="me@gmail.com", file_text=""):
        self.address = address
        self.file_text = file_text
        self.sent = None

    def my_gmail_address(self):
        return self.address

    def last_written(self):
        return None

    def read_file(self, path):
        return self.file_text

    def gmail_send(self, to, subject, body):
        self.sent = {"to": to, "subject": subject, "body": body}
        return f"Sent the email to {to}."


class ComposeTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.skill = _skill("send_email")

    def test_asks_for_recipient_when_missing(self):
        out = self.skill.run("send an email with a joke", {"actions": StubActions(), "llm": StubLLM([])})
        self.assertIn("Who should I send it to", out)

    def test_myself_resolves_to_own_address_and_body_is_generated(self):
        act = StubActions(address="pradeep@gmail.com")
        out = self.skill.run("write an email to myself with a secret message",
                             {"actions": act, "llm": StubLLM(["The eagle flies at midnight.", "Secret"])})
        self.assertEqual(act.sent["to"], "pradeep@gmail.com")
        self.assertIn("eagle", act.sent["body"])
        self.assertIn("Sent the email", out)

    def test_uses_a_draft_file_for_the_body(self):
        draft = self.tmp / "email_draft.txt"
        draft.write_text("Dear Shiv,\n\nPlease find the report attached.\n\nRegards", encoding="utf-8")
        act = StubActions(file_text=draft.read_text(encoding="utf-8"))
        out = self.skill.run("send that draft to shiv@x.org",
                             {"actions": act, "llm": StubLLM(["Report"]), "focus": {"file": str(draft)}})
        self.assertEqual(act.sent["to"], "shiv@x.org")
        self.assertIn("Please find the report", act.sent["body"])
        self.assertIn("Sent the email", out)

    def test_explicit_subject_is_used(self):
        act = StubActions()
        self.skill.run("email bob@x.com subject: Meeting saying lets meet at 3",
                       {"actions": act, "llm": StubLLM(["Let's meet at 3."])})
        self.assertEqual(act.sent["subject"], "Meeting")


class DeferTests(IsolatedCase):
    def test_gmail_search_defers_compose_requests(self):
        gs = _skill("gmail_search")
        self.assertIsNone(gs.run("send an email to bob@x.com about lunch", {"actions": StubActions()}))
        self.assertIsNone(gs.run("email myself a reminder to call the bank", {"actions": StubActions()}))


if __name__ == "__main__":
    unittest.main()
