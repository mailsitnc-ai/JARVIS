"""Chrome DOM control: the CDP controller logic, broker gating, and skill dispatch (mocked, no real Chrome)."""
import json
import unittest
from unittest import mock

from core.actions import ActionBroker, Blocked
from core.browser import BrowserError, ChromeController
from core.permissions import PermissionRegistry
from tests.helpers import IsolatedCase


class FakeWS:
    """A stand-in DevTools websocket: replies to each command, optionally emitting an event first."""

    def __init__(self, responder, prefix_events=0):
        self.responder = responder
        self.sent = []
        self._queue = []
        self._prefix_events = prefix_events

    def send(self, raw):
        msg = json.loads(raw)
        self.sent.append(msg)
        for _ in range(self._prefix_events):        # noise events with no id, must be skipped
            self._queue.append(json.dumps({"method": "Page.frameNavigated", "params": {}}))
        self._queue.append(json.dumps({"id": msg["id"], "result": self.responder(msg["method"], msg.get("params", {}))}))

    def recv(self):
        return self._queue.pop(0)

    def close(self):
        pass


def _controller(responder, prefix_events=0):
    c = ChromeController()
    c._ws = FakeWS(responder, prefix_events)
    c._msg_id = 0
    return c


class ControllerTests(unittest.TestCase):
    def test_cmd_matches_ids_and_skips_events(self):
        c = _controller(lambda method, params: {"ok": method}, prefix_events=2)
        self.assertEqual(c._cmd("Page.enable"), {"ok": "Page.enable"})

    def test_evaluate_returns_the_value(self):
        c = _controller(lambda m, p: {"result": {"value": "hello"}})
        self.assertEqual(c.evaluate("document.title"), "hello")

    def test_evaluate_raises_on_js_exception(self):
        c = _controller(lambda m, p: {"exceptionDetails": {"text": "ReferenceError: x"}})
        with self.assertRaises(BrowserError):
            c.evaluate("x")

    def test_click_and_type_report_success(self):
        def responder(method, params):
            expr = params.get("expression", "")
            value = True if (".click()" in expr or "dispatchEvent" in expr) else None
            return {"result": {"value": value}}
        c = _controller(responder)
        self.assertTrue(c.click("Sign in"))
        self.assertTrue(c.type_text("hello", submit=False))

    def test_navigate_waits_for_ready(self):
        def responder(method, params):
            if method == "Runtime.evaluate" and "readyState" in params.get("expression", ""):
                return {"result": {"value": "complete"}}
            return {}
        c = _controller(responder)
        c.navigate("https://example.com", wait=2)  # returns once readyState == complete, no exception
        self.assertTrue(any(m["method"] == "Page.navigate" for m in c._ws.sent))

    def test_cmd_raises_on_cdp_error(self):
        c = _controller(lambda m, p: {})
        c._ws.send = lambda raw: c._ws._queue.append(
            json.dumps({"id": json.loads(raw)["id"], "error": {"message": "boom"}}))
        with self.assertRaises(BrowserError):
            c._cmd("Page.navigate", {"url": "x"})


class BrokerGatingTests(IsolatedCase):
    def test_browser_actions_dry_run_without_touching_chrome(self):
        broker = ActionBroker(dry_run=True)
        self.assertIn("Would open", broker.browser_open("example.com"))
        self.assertIn("unavailable", broker.browser_read())
        self.assertIn("Would click", broker.browser_click("Sign in"))
        self.assertIn("Would type", broker.browser_type("hello"))

    def test_browser_is_denied_when_blocked(self):
        perms = PermissionRegistry(self.tmp / "p.json")
        perms.set("browser", "deny")
        with self.assertRaises(Blocked):
            ActionBroker(permissions=perms).browser_read()

    def test_browser_capability_exists_and_autonomy_allows_it(self):
        perms = PermissionRegistry(self.tmp / "p.json")
        self.assertEqual(perms.state("browser"), "ask")
        perms.set_autonomy(True)
        self.assertEqual(perms.state("browser"), "allow")  # not privacy-sensitive: unleashed allows it


class _FakeActions:
    def __init__(self):
        self.calls = []

    def _rec(self, name, *args):
        self.calls.append((name, args))
        return f"{name}:{args}"

    def browser_open(self, url):
        return self._rec("open", url)

    def browser_click(self, target):
        return self._rec("click", target)

    def browser_type(self, text, submit=False):
        return self._rec("type", text, submit)

    def browser_read(self, max_chars=6000):
        return "Example Domain. This domain is for use in examples."

    def browser_screenshot(self, path=None):
        return self._rec("shot")


class SkillDispatchTests(IsolatedCase):
    def _skill(self):
        from core.config import SKILLS_DIR
        from core.skill_loader import SkillRegistry
        reg = SkillRegistry(SKILLS_DIR)
        reg.reload()
        return reg.get("browser_control")

    def _run(self, request, actions=None):
        return self._skill().run(request, {"actions": actions or _FakeActions()})

    def test_browse_to_opens(self):
        act = _FakeActions()
        self._run("browse to news.ycombinator.com", act)
        self.assertEqual(act.calls[0][0], "open")

    def test_go_to_bed_is_not_a_browse_command(self):
        self.assertIsNone(self._run("go to bed"))

    def test_click_dispatch(self):
        act = _FakeActions()
        self._run("click the Sign in button", act)
        self.assertEqual(act.calls[0], ("click", ("Sign in",)))

    def test_type_and_search_submits(self):
        act = _FakeActions()
        self._run("type hello world in the search box and press enter", act)
        self.assertEqual(act.calls[0][0], "type")
        self.assertEqual(act.calls[0][1][0], "hello world")
        self.assertTrue(act.calls[0][1][1])  # submit=True

    def test_read_page(self):
        out = self._run("read the page")
        self.assertIn("Example Domain", out)

    def test_dry_run_messages(self):
        skill = self._skill()
        self.assertIn("Would browse", skill.run("browse to example.com", {"dry_run": True}))
        self.assertIn("Would read", skill.run("read this page", {"dry_run": True}))


if __name__ == "__main__":
    unittest.main()
