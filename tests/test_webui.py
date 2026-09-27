"""The new interface: what it serves, what it streams, and what it refuses."""
import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import webui  # noqa: E402


def get(url, data=None):
    request = urllib.request.Request(
        url, data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urllib.request.urlopen(request, timeout=10) as answer:
            return answer.status, answer.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class DeskTests(unittest.TestCase):
    """The state behind the glass, with no HTTP involved."""

    def test_a_request_becomes_a_task_at_once(self):
        done = threading.Event()
        desk = webui.Desk(lambda text: done.wait(2) or "80 percent, sir.")
        task = desk.submit("what's my battery")
        self.assertEqual(task["state"], "running")
        self.assertEqual(task["text"], "what's my battery")
        done.set()

    def test_the_answer_lands_on_the_task_and_the_feed(self):
        desk = webui.Desk(lambda text: "It's 21:20.")
        watching = desk.feed.join()
        desk.submit("what time is it")
        kinds = []
        for _ in range(3):
            kinds.append(json.loads(watching.get(timeout=3))["kind"])
        self.assertIn("answer", kinds)
        self.assertEqual(desk.state()["tasks"][0]["answer"], "It's 21:20.")

    def test_a_task_that_blows_up_is_shown_as_failed_not_lost(self):
        def explode(_text):
            raise RuntimeError("the skill fell over")

        desk = webui.Desk(explode)
        desk.submit("do the thing")
        time.sleep(0.4)
        task = desk.state()["tasks"][0]
        self.assertEqual(task["state"], "failed")
        self.assertIn("fell over", task["answer"])

    def test_nothing_to_do_is_turned_down(self):
        self.assertIn("error", webui.Desk(lambda t: "x").submit("   "))

    def test_the_working_out_is_attached_to_what_is_running(self):
        """Steps are the proof: they must land on the task they belong to."""
        holding = threading.Event()
        desk = webui.Desk(lambda text: holding.wait(2) or "done")
        desk.submit("build me something")
        desk.step("route", "Using skill 'current_time'")
        self.assertEqual(desk.state()["tasks"][0]["steps"][0]["text"], "Using skill 'current_time'")
        holding.set()

    def test_a_watcher_that_stopped_reading_does_not_block_the_engine(self):
        desk = webui.Desk(lambda t: "ok")
        desk.feed.join()                      # joined and never read
        for _ in range(400):
            desk.feed.push("step", text="x")  # must not hang
        self.assertTrue(True)

    def test_the_four_lights_are_reported(self):
        names = [row["name"] for row in webui.Desk(lambda t: "x").systems()]
        self.assertEqual(names, ["AI", "Voice", "Memory", "Cloud"])


class ServedTests(unittest.TestCase):
    def setUp(self):
        self.desk = webui.Desk(lambda text: f"you said {text}")
        self.server = webui.Server(self.desk, port=0, secret="test-secret")
        self.server.start()
        self.addCleanup(self.server.stop)
        self.base = f"http://127.0.0.1:{self.server.port}"

    def test_the_page_needs_the_secret(self):
        self.assertEqual(get(f"{self.base}/")[0], 403)
        status, body = get(f"{self.base}/?k=test-secret")
        self.assertEqual(status, 200)
        self.assertIn(b"Ask JARVIS", body)
        self.assertNotIn(b"__SECRET__", body)      # the page can talk back

    def test_it_listens_only_on_this_mac(self):
        self.assertEqual(self.server.httpd.server_address[0], "127.0.0.1")

    def test_state_comes_back_as_json(self):
        status, body = get(f"{self.base}/state?k=test-secret")
        self.assertEqual(status, 200)
        self.assertIn("systems", json.loads(body))

    def test_asking_starts_a_task(self):
        """The page is answered the moment the work starts - a quick skill may already be done."""
        status, body = get(f"{self.base}/ask?k=test-secret", data={"text": "hello"})
        self.assertEqual(status, 200)
        task = json.loads(body)
        self.assertEqual(task["text"], "hello")
        self.assertIn(task["state"], ("running", "done"))
        self.assertTrue(task["id"])

    def test_asking_without_the_secret_is_refused(self):
        self.assertEqual(get(f"{self.base}/ask", data={"text": "hello"})[0], 403)

    def test_the_styles_and_script_are_served(self):
        for path in ("/app.css", "/app.js"):
            status, body = get(f"{self.base}{path}?k=test-secret")
            self.assertEqual(status, 200, path)
            self.assertTrue(body)


if __name__ == "__main__":
    unittest.main()
