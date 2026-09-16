import os
import unittest
from unittest import mock

from core.actions import ActionBroker, ActionRequest, Blocked
from core.config import SKILLS_DIR, load_settings
from core.orchestrator import Jarvis
from core.permissions import PermissionRegistry
from core.skill_loader import SkillRegistry
from memory.store import MemoryStore
from tests.helpers import FakeLLM, IsolatedCase

PING_SKILL = ('SKILL = {"name": "ping_site", "description": "d", "triggers": ["\\\\bping\\\\b"], "origin": "evolved"}\n\n'
              'def run(request, context):\n    return context["actions"].open_url("https://example.com")\n')


class PermissionTests(IsolatedCase):
    def test_defaults_to_ask_and_persists_changes(self):
        reg = PermissionRegistry(self.tmp / "permissions.json")
        self.assertEqual(reg.state("open"), "ask")
        reg.set("open", "allow")
        reg.set("screen", "deny")
        self.assertEqual(PermissionRegistry(self.tmp / "permissions.json").state("open"), "allow")
        self.assertEqual(PermissionRegistry(self.tmp / "permissions.json").state("screen"), "deny")

    def test_reset_and_unknown(self):
        reg = PermissionRegistry(self.tmp / "permissions.json")
        reg.set("open", "allow")
        reg.reset("open")
        self.assertEqual(reg.state("open"), "ask")
        with self.assertRaises(KeyError):
            reg.set("nope", "allow")
        with self.assertRaises(ValueError):
            reg.set("open", "maybe")

    def test_camera_is_privacy_exempt_from_autonomy(self):
        reg = PermissionRegistry(self.tmp / "permissions.json")
        reg.set_autonomy(True)
        self.assertEqual(reg.state("open"), "allow")     # normal capability: unleashed
        self.assertEqual(reg.state("camera"), "ask")     # sensitive: still gated even while unleashed
        reg.set("camera", "allow")
        self.assertEqual(reg.state("camera"), "allow")   # unless you explicitly allow it

    def test_autonomy_allows_everything_and_persists(self):
        reg = PermissionRegistry(self.tmp / "permissions.json")
        reg.set("screen", "deny")  # even an explicit deny is overridden while unleashed
        self.assertFalse(reg.autonomy())
        reg.set_autonomy(True)
        self.assertTrue(reg.autonomy())
        for cap in ("open", "screen", "run_command", "network", "packages"):
            self.assertEqual(reg.state(cap), "allow")
        # persists across a fresh registry (survives a restart)
        again = PermissionRegistry(self.tmp / "permissions.json")
        self.assertTrue(again.autonomy())
        self.assertEqual(again.state("run_command"), "allow")
        # turning it off restores the underlying grants
        again.set_autonomy(False)
        self.assertEqual(again.state("screen"), "deny")
        self.assertEqual(again.state("open"), "ask")


class ActionBrokerDryTests(IsolatedCase):
    def test_dry_run_simulates_and_never_touches_the_system(self):
        broker = ActionBroker(dry_run=True)
        with mock.patch("core.actions.webbrowser.open") as browser, \
                mock.patch("core.actions.os.startfile") as startfile, \
                mock.patch("core.actions.subprocess.run") as run:
            self.assertEqual(broker.open_app("notepad"), "Would open Notepad.")
            self.assertEqual(broker.open_url("example.com"), "Would open https://example.com in your browser.")
            self.assertIn("Would capture", broker.screenshot())
            self.assertIn("unavailable during verification", broker.read_file("x.txt"))
            self.assertIn("unavailable during verification", broker.run_command(["whoami"]))
            self.assertIn("Would show a popup", broker.notify("hi", "T"))  # no window during verification
            browser.assert_not_called()
            startfile.assert_not_called()
            run.assert_not_called()
        self.assertEqual(broker.performed, [])
        self.assertEqual(len(broker.simulated), 6)


class ActionBrokerLiveTests(IsolatedCase):
    def broker(self, confirm=None):
        return ActionBroker(dry_run=False, permissions=PermissionRegistry(self.tmp / "p.json"), confirm=confirm)

    def test_allow_state_runs_without_asking(self):
        perms = PermissionRegistry(self.tmp / "p.json")
        perms.set("open", "allow")
        asked = []
        broker = ActionBroker(permissions=perms, confirm=lambda req: asked.append(req) or "deny")
        with mock.patch("core.actions.webbrowser.open") as browser:
            self.assertEqual(broker.open_url("example.com"), "Opening https://example.com")
            browser.assert_called_once()
        self.assertEqual(asked, [])

    def test_last_written_remembers_the_created_file(self):
        perms = PermissionRegistry(self.tmp / "p.json")
        perms.set("write_files", "allow")
        broker = ActionBroker(permissions=perms, state_dir=self.tmp)
        self.assertIsNone(broker.last_written())  # nothing written yet
        target = self.tmp / "note.txt"
        broker.write_file(str(target), "hello")
        self.assertEqual(broker.last_written(), target)  # remembered for "open it"
        # a fresh broker (new request) still sees it, via the state file
        self.assertEqual(ActionBroker(permissions=perms, state_dir=self.tmp).last_written(), target)

    def test_open_url_prefers_the_chosen_browser(self):
        perms = PermissionRegistry(self.tmp / "p.json")
        perms.set("open", "allow")
        broker = ActionBroker(permissions=perms, browser="chrome")
        with mock.patch("core.actions.shutil.which", return_value=r"C:\chrome.exe"), \
                mock.patch("core.actions.subprocess.Popen") as popen, \
                mock.patch("core.actions.webbrowser.open") as wb:
            broker.open_url("example.com")
            popen.assert_called_once()
            self.assertEqual(popen.call_args[0][0], [r"C:\chrome.exe", "https://example.com"])
            wb.assert_not_called()   # went to Chrome, not the OS default
        # default/None falls back to the OS default browser
        default = ActionBroker(permissions=perms, browser="default")
        with mock.patch("core.actions.webbrowser.open") as wb2:
            default.open_url("example.com")
            wb2.assert_called_once()

    def test_capture_camera_dry_run_and_gate(self):
        self.assertIn("Would take a webcam photo", ActionBroker(dry_run=True).capture_camera())
        perms = PermissionRegistry(self.tmp / "p.json")
        perms.set("camera", "deny")
        with self.assertRaises(Blocked):
            ActionBroker(permissions=perms).capture_camera()

    def test_run_python_launches_the_interpreter(self):
        perms = PermissionRegistry(self.tmp / "p.json")
        perms.set("run_command", "allow")
        f = self.tmp / "app.py"
        f.write_text("print('hi')", encoding="utf-8")
        broker = ActionBroker(permissions=perms)
        with mock.patch("core.actions.subprocess.Popen") as popen:
            out = broker.run_python(str(f))
            popen.assert_called_once()
        self.assertIn("Running app.py", out)
        self.assertIn("Would run", ActionBroker(dry_run=True).run_python("x.py"))

    def test_organize_dir_sorts_files_into_type_folders(self):
        perms = PermissionRegistry(self.tmp / "p.json")
        perms.set("write_files", "allow")
        folder = self.tmp / "Downloads"
        folder.mkdir()
        for fn in ("a.png", "b.pdf", "c.mp3", "d.unknownext"):
            (folder / fn).write_text("x", encoding="utf-8")
        broker = ActionBroker(permissions=perms)
        out = broker.organize_dir(str(folder))
        self.assertIn("Organized 4", out)
        self.assertTrue((folder / "Images" / "a.png").is_file())
        self.assertTrue((folder / "Documents" / "b.pdf").is_file())
        self.assertTrue((folder / "Audio" / "c.mp3").is_file())
        self.assertTrue((folder / "Other" / "d.unknownext").is_file())
        self.assertIn("already tidy", broker.organize_dir(str(folder)))  # second pass: nothing loose left

    def test_autonomy_runs_every_capability_without_asking(self):
        perms = PermissionRegistry(self.tmp / "p.json")
        perms.set_autonomy(True)
        asked = []
        broker = ActionBroker(permissions=perms, confirm=lambda req: asked.append(req) or "deny")
        with mock.patch("core.actions.webbrowser.open") as browser:
            self.assertEqual(broker.open_url("example.com"), "Opening https://example.com")
            browser.assert_called_once()
        self.assertEqual(asked, [])  # unleashed: never stopped to ask

    def test_deny_state_blocks_before_doing_anything(self):
        perms = PermissionRegistry(self.tmp / "p.json")
        perms.set("open", "deny")
        broker = ActionBroker(permissions=perms, confirm=lambda req: "always")
        with mock.patch("core.actions.webbrowser.open") as browser:
            with self.assertRaises(Blocked):
                broker.open_url("example.com")
            browser.assert_not_called()

    def test_ask_once_runs_but_does_not_persist(self):
        broker = self.broker(confirm=lambda req: "once")
        with mock.patch("core.actions.webbrowser.open"):
            broker.open_url("example.com")
        self.assertEqual(PermissionRegistry(self.tmp / "p.json").state("open"), "ask")

    def test_ask_always_persists_allow(self):
        broker = self.broker(confirm=lambda req: "always")
        with mock.patch("core.actions.webbrowser.open"):
            broker.open_url("example.com")
        self.assertEqual(PermissionRegistry(self.tmp / "p.json").state("open"), "allow")

    def test_declining_raises_blocked(self):
        broker = self.broker(confirm=lambda req: "deny")
        with self.assertRaises(Blocked):
            broker.read_file(str(self.tmp / "whatever.txt"))

    def test_confirmer_sees_a_readable_summary(self):
        seen = []
        broker = self.broker(confirm=lambda req: seen.append(req) or "deny")
        with self.assertRaises(Blocked):
            broker.open_app("notepad")
        self.assertIsInstance(seen[0], ActionRequest)
        self.assertEqual(seen[0].capability, "open")
        self.assertEqual(seen[0].summary, "Open Notepad")


class ScreenshotMemoryTests(IsolatedCase):
    def test_remembers_and_recalls_the_last_screenshot(self):
        pics = self.tmp / "pics"
        pics.mkdir()
        broker = ActionBroker(state_dir=self.tmp, pictures_dir=pics)
        self.assertIsNone(broker.last_screenshot())
        shot = self.tmp / "JARVIS-shot.png"
        shot.write_bytes(b"png")
        broker._remember_screenshot(shot)
        self.assertEqual(ActionBroker(state_dir=self.tmp, pictures_dir=pics).last_screenshot(), shot)

    def test_reveal_is_gated(self):
        shot = self.tmp / "shot.png"
        shot.write_bytes(b"png")
        broker = ActionBroker(permissions=PermissionRegistry(self.tmp / "p.json"), confirm=lambda req: "deny")
        with mock.patch("core.actions.subprocess.run") as run:
            with self.assertRaises(Blocked):
                broker.reveal(str(shot))
            run.assert_not_called()


class NetworkActionTests(IsolatedCase):
    def test_dry_run_returns_empty_json_and_does_not_touch_the_network(self):
        broker = ActionBroker(dry_run=True)
        with mock.patch("urllib.request.urlopen") as urlopen:
            self.assertEqual(broker.http_request("https://example.com/api"), "{}")
            urlopen.assert_not_called()

    def test_network_is_gated(self):
        broker = ActionBroker(permissions=PermissionRegistry(self.tmp / "p.json"), confirm=lambda req: "deny")
        with mock.patch("urllib.request.urlopen") as urlopen:
            with self.assertRaises(Blocked):
                broker.http_request("https://example.com")
            urlopen.assert_not_called()


class PackageInstallTests(IsolatedCase):
    def test_dry_run_and_validation(self):
        broker = ActionBroker(dry_run=True)
        self.assertEqual(broker.install_package("beautifulsoup4"), "Would install beautifulsoup4.")
        # obviously unsafe specs are refused outright, before any gate
        self.assertIn("Refusing", broker.install_package("../evil"))
        self.assertIn("Refusing", broker.install_package("git+https://x/y.git"))

    def test_install_is_gated(self):
        broker = ActionBroker(permissions=PermissionRegistry(self.tmp / "p.json"), confirm=lambda req: "deny")
        with mock.patch("core.actions.subprocess.run") as run:
            with self.assertRaises(Blocked):
                broker.install_package("beautifulsoup4")
            run.assert_not_called()


class OrchestratorGatingTests(IsolatedCase):
    def make(self, confirm):
        skills = self.tmp / "skills"
        skills.mkdir()
        (skills / "ping_site.py").write_text(PING_SKILL, encoding="utf-8")
        return Jarvis(load_settings(), llm=FakeLLM(), registry=SkillRegistry(skills),
                      memory=MemoryStore(self.tmp / "memory"), archive_dir=self.tmp / "archive",
                      permissions=PermissionRegistry(self.tmp / "p.json"), confirm=confirm)

    def test_declined_action_reports_without_repair(self):
        jarvis = self.make(confirm=lambda req: "deny")
        with mock.patch("core.actions.webbrowser.open") as browser:
            reply = jarvis.handle("ping")
            browser.assert_not_called()
        self.assertEqual(reply.skill, "ping_site")
        self.assertIn("didn't approve", reply.text)

    def test_approved_action_runs(self):
        os.environ["JARVIS_WINDOW__BROWSER"] = "default"  # use the OS default so webbrowser.open is the path
        jarvis = self.make(confirm=lambda req: "once")
        with mock.patch("core.actions.webbrowser.open") as browser:
            reply = jarvis.handle("ping")
            browser.assert_called_once()
        self.assertEqual(reply.text, "Opening https://example.com")


class BuiltinSkillActionTests(IsolatedCase):
    def test_screenshot_skill_asks_before_capturing(self):
        registry = SkillRegistry(SKILLS_DIR)
        registry.reload()
        skill = registry.get("screenshot")
        broker = ActionBroker(permissions=PermissionRegistry(self.tmp / "p.json"), confirm=lambda req: "deny")
        with mock.patch("core.actions.subprocess.run") as run:
            with self.assertRaises(Blocked):  # the orchestrator turns this into a friendly message
                skill.run("take a screenshot", {"actions": broker})
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
