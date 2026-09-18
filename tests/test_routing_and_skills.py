import os
import shutil
import unittest
from unittest import mock

from core.config import SKILLS_DIR, load_settings
from core.orchestrator import Jarvis
from core.skill_loader import SkillRegistry
from memory.store import MemoryStore
from tests.helpers import FakeLLM, IsolatedCase

BUILTIN_SKILLS = ("temperature_converter", "current_time", "calculator", "system_status", "open_app",
                  "web_search", "screenshot", "browser_tab", "show_screenshot", "speak",
                  "drive_search", "gmail_search", "popup", "write_file", "open_last", "organize_files",
                  "gmail_organize", "camera", "observe_screen", "record_video", "browser_control",
                  "fix_code", "send_email")


class RoutingTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        # Only the built-in skills: skills JARVIS has evolved on this machine would change routing.
        self.skills_dir = self.tmp / "skills"
        self.skills_dir.mkdir()
        for name in BUILTIN_SKILLS:
            shutil.copy(SKILLS_DIR / f"{name}.py", self.skills_dir)

    def make(self, llm=None):
        return Jarvis(load_settings(), llm=llm or FakeLLM(), registry=SkillRegistry(self.skills_dir),
                      memory=MemoryStore(self.tmp / "memory"), archive_dir=self.tmp / "archive")

    def test_no_trigger_goes_to_evolution_without_asking_the_llm(self):
        llm = FakeLLM()
        jarvis = self.make(llm)
        skill, route = jarvis.route("count the vowels in a word")
        self.assertIsNone(skill)
        self.assertEqual(route, "no-trigger")
        self.assertEqual(llm.calls, [])

    def test_builtin_skills_route_and_answer(self):
        jarvis = self.make()
        self.assertEqual(jarvis.handle("convert 30 celsius to fahrenheit").text, "30 °C = 86 °F")
        self.assertEqual(jarvis.handle("what is 12 * (3 + 4)?").text, "12 * (3 + 4) = 84")
        self.assertEqual(jarvis.handle("2^10").text, "2^10 = 1024")
        self.assertEqual(jarvis.route("what time is it")[0].name, "current_time")
        self.assertEqual(jarvis.route("how much ram is free")[0].name, "system_status")

    def test_unhandled_request_with_evolution_off(self):
        os.environ["JARVIS_EVOLUTION__ENABLED"] = "false"
        jarvis = self.make()
        reply = jarvis.handle("download the quarterly report")  # actionable, no skill, evolution off
        self.assertEqual(reply.route, "no-skill")

    def test_open_app_honours_dry_run(self):
        registry = SkillRegistry(SKILLS_DIR)
        registry.reload()
        skill = registry.get("open_app")
        self.assertEqual(skill.run("open notepad", {"dry_run": True}), "Would open Notepad.")
        self.assertEqual(skill.run("open github.com", {"dry_run": True}), "Would open https://github.com in your browser.")

    def test_all_builtin_skills_load(self):
        registry = SkillRegistry(SKILLS_DIR)
        registry.reload()
        self.assertEqual({n: e for n, e in registry.errors.items()}, {})
        for name in BUILTIN_SKILLS:
            self.assertIn(name, registry.skills)

    def test_screenshot_and_browser_tab_route(self):
        jarvis = self.make()
        self.assertEqual(jarvis.route("take a screenshot")[0].name, "screenshot")
        self.assertEqual(jarvis.route("open a new tab on my chrome")[0].name, "browser_tab")

    def test_popup_routes_and_extracts_message(self):
        jarvis = self.make()
        self.assertEqual(jarvis.route("show a popup saying hello there")[0].name, "popup")
        self.assertEqual(jarvis.route("pop up a message box: build done")[0].name, "popup")
        skill = jarvis.registry.get("popup")
        self.assertEqual(skill.run("popup saying hello there", {"dry_run": True}), 'Would show a popup: "hello there"')
        self.assertEqual(skill.run("show a popup", {"dry_run": True}), "What should the popup say?")

    def test_gmail_organize_parses_action_query_and_label(self):
        from skills.gmail_organize import run as gm
        ctx = {"dry_run": True}
        self.assertEqual(gm("archive emails from noreply@x.com", ctx),
                         "Would archive emails matching 'from:noreply@x.com'.")
        self.assertEqual(gm("mark promotions emails as read", ctx),
                         "Would read emails matching 'category:promotions'.")
        self.assertEqual(gm("label emails from boss@x.com as Work", ctx),
                         "Would label emails matching 'from:boss@x.com' as 'Work'.")
        self.assertIn("trash", gm("trash emails older than 60 days", ctx))
        self.assertIn("What label", gm("label emails from x@y.com", ctx))        # missing label
        self.assertIn("what to do", gm("do something with my inbox", ctx).lower())  # no action

    def test_write_file_routes_and_resolves_path(self):
        jarvis = self.make()
        self.assertEqual(jarvis.route("write a python script that sorts a list and save it as sort.py")[0].name,
                         "write_file")
        self.assertEqual(jarvis.route("make an html landing page and save it as index.html")[0].name, "write_file")
        skill = jarvis.registry.get("write_file")
        self.assertEqual(skill.run("write a snake game and save it as snake.py", {"dry_run": True}),
                         "Would generate and write snake.py.")
        from skills.write_file import _target_path
        target, name = _target_path("write x and save it to my desktop as game.py")
        self.assertEqual((name, target.parent.name), ("game.py", "Desktop"))

    def test_open_it_defers_to_last_created_file(self):
        jarvis = self.make()
        from skills.open_app import run as open_app_run
        for phrase in ("open it", "run it", "open that", "open the file you just made"):
            names = [s.name for s in jarvis.candidates(phrase)]
            self.assertIn("open_last", names, phrase)
        # open_app declines a pronoun target, so the cascade reaches open_last
        self.assertIsNone(open_app_run("open it", {"dry_run": True}))
        self.assertIsNone(open_app_run("open that", {"dry_run": True}))
        # a real app name still resolves in open_app
        self.assertEqual(open_app_run("open notepad", {"dry_run": True}), "Would open Notepad.")

    def test_open_a_filename_opens_the_file_not_a_website(self):
        from core.actions import ActionBroker, web_url_for
        from skills.open_app import run as open_app_run
        self.assertIsNone(web_url_for("frenchjokes.py"))            # a file, not a site
        self.assertEqual(web_url_for("google.com"), "https://google.com")  # a domain still is
        self.assertEqual(web_url_for("example.com/page"), "https://example.com/page")
        ctx = {"dry_run": True, "actions": ActionBroker(dry_run=True)}
        f = self.tmp / "frenchjokes.py"
        f.write_text("print('salut')", encoding="utf-8")
        self.assertIn("Would open", open_app_run(f"open {f}", ctx))  # existing full path -> file
        self.assertIn("couldn't find a file", open_app_run("open nope_zzz.py", ctx))  # not a browser tab
        self.assertEqual(open_app_run("open notepad", ctx), "Would open Notepad.")  # apps still work
        self.assertIn("docs.google.com", open_app_run("open google docs", ctx))  # web apps still work

    def test_google_skills_route(self):
        jarvis = self.make()
        self.assertEqual(jarvis.route("find the budget file in my drive")[0].name, "drive_search")
        self.assertEqual(jarvis.route("any emails from alice about the invoice")[0].name, "gmail_search")

    def test_show_screenshot_does_not_recapture(self):
        jarvis = self.make()
        self.assertEqual(jarvis.route("show me the screenshot")[0].name, "show_screenshot")
        self.assertEqual(jarvis.route("open the folder where the screenshot is saved")[0].name, "show_screenshot")

    def test_open_app_resolves_web_apps_and_browser_suffix(self):
        registry = SkillRegistry(SKILLS_DIR)
        registry.reload()
        skill = registry.get("open_app")
        self.assertEqual(skill.run("open google docs", {"dry_run": True}),
                         "Would open https://docs.google.com in your browser.")
        # trailing "on my chrome" is stripped, not treated as part of the name
        self.assertEqual(skill.run("open gmail on my existing chrome", {"dry_run": True}),
                         "Would open https://mail.google.com in your browser.")

    def test_browser_tab_opens_named_site(self):
        registry = SkillRegistry(SKILLS_DIR)
        registry.reload()
        tab = registry.get("browser_tab")
        self.assertEqual(tab.run("open a new tab for google docs on my existing chrome", {"dry_run": True}),
                         "Would open https://docs.google.com in your browser.")
        self.assertIn("Would open", tab.run("open a new tab", {"dry_run": True}))  # blank -> default page

    def test_open_app_defers_non_app_phrases(self):
        registry = SkillRegistry(SKILLS_DIR)
        registry.reload()
        skill = registry.get("open_app")
        # a sentence about a folder/screenshot is not an app name -> decline so another skill handles it
        self.assertIsNone(skill.run("open the folder where the screenshot is saved", {"dry_run": True}))
        # a plain name we can't find -> say so, don't return None (which would spawn a clone skill)
        self.assertIn("couldn't find", skill.run("open antigravity", {"dry_run": True}))

    def test_multi_step_requests_are_chained(self):
        jarvis = self.make()
        reply = jarvis.handle("what time is it and 2+2")
        self.assertEqual(reply.route, "chain")
        self.assertIn("= 4", reply.text)
        self.assertIn("+", reply.skill or "")  # e.g. "current_time + calculator"

    def test_chain_strips_filler_verbs(self):
        jarvis = self.make()
        reply = jarvis.handle("what time is it and operate 1+1")
        self.assertEqual(reply.route, "chain")
        self.assertIn("= 2", reply.text)

    def test_correction_reroutes_to_a_skill(self):
        # open_app's trigger is anchored to the start, so the whole "no i meant..." string doesn't match
        # it directly; only after stripping the correction does it route.
        jarvis = self.make()
        reply = jarvis.handle("no i meant open notepad")
        self.assertEqual((reply.route, reply.skill), ("corrected", "open_app"))

    def test_small_talk_gets_a_canned_reply_without_the_model(self):
        jarvis = self.make()  # FakeLLM with no scripted responses: any model call would raise
        self.assertEqual(jarvis.handle("thanks").route, "chat")
        self.assertEqual(jarvis.handle("thanks").text, "Anytime.")
        caps = jarvis.handle("what can you do")
        self.assertEqual(caps.route, "chat")
        self.assertIn("temperature", caps.text.lower())

    def test_speak_routes_and_extracts_text(self):
        jarvis = self.make()
        self.assertEqual(jarvis.route("say good morning")[0].name, "speak")
        self.assertEqual(jarvis.route("read this aloud: hello world")[0].name, "speak")
        registry = SkillRegistry(SKILLS_DIR)
        registry.reload()
        speak = registry.get("speak")
        self.assertEqual(speak.run("read this text outloud : My name is JARVIS", {"dry_run": True}),
                         'Would say: "My name is JARVIS"')
        self.assertEqual(speak.run("say hello there", {"dry_run": True}), 'Would say: "hello there"')

    def test_politeness_is_stripped_before_routing(self):
        jarvis = self.make()
        reply = jarvis.handle("can you open notepad")  # wrapper hides the command; open_app is anchored to ^
        self.assertEqual(reply.skill, "open_app")
        reply2 = jarvis.handle("please say hi")
        self.assertEqual(reply2.skill, "speak")

    def test_hot_reload_picks_up_new_changed_and_deleted_files(self):
        folder = self.tmp / "hot_reload_skills"
        folder.mkdir()
        path = folder / "greet.py"
        path.write_text('SKILL = {"name": "greet", "description": "d", "triggers": ["hello"]}\n'
                        'def run(request, context):\n    return "v1"\n', encoding="utf-8")
        registry = SkillRegistry(folder)
        self.assertEqual(registry.reload(), ["greet"])
        self.assertEqual(registry.get("greet").run("hello", {}), "v1")
        path.write_text(path.read_text(encoding="utf-8").replace("v1", "version2"), encoding="utf-8")
        registry.reload()
        self.assertEqual(registry.get("greet").run("hello", {}), "version2")
        path.unlink()
        registry.reload()
        self.assertIsNone(registry.get("greet"))


class SkillCompositionTests(IsolatedCase):
    def test_context_run_delegates_to_another_skill(self):
        skills_dir = self.tmp / "skills"
        skills_dir.mkdir()
        shutil.copy(SKILLS_DIR / "calculator.py", skills_dir)
        # a skill that composes: it answers by calling the calculator via context["run"]
        (skills_dir / "double.py").write_text(
            "SKILL = {'name':'double','description':'double a number','triggers':['\\\\bdouble\\\\b'],'origin':'evolved'}\n"
            "import re\n"
            "def run(request, context):\n"
            "    n = re.search(r'\\d+', request).group(0)\n"
            "    return 'via calculator: ' + context['run'](f'{n}*2')\n", encoding="utf-8")
        jarvis = Jarvis(load_settings(), llm=FakeLLM(), registry=SkillRegistry(skills_dir),
                        memory=MemoryStore(self.tmp / "memory"))
        reply = jarvis.handle("double 21")
        self.assertEqual(reply.text, "via calculator: 21*2 = 42")


class UnderstandingTests(IsolatedCase):
    def make(self, llm, confirm=None):
        skills_dir = self.tmp / "skills"
        skills_dir.mkdir()
        for name in ("open_app", "current_time", "web_search"):
            shutil.copy(SKILLS_DIR / f"{name}.py", skills_dir)
        # approve the plan review, deny the individual actions (so nothing really opens in tests)
        confirm = confirm or (lambda req: "once" if req.capability == "plan" else "deny")
        jarvis = Jarvis(load_settings(), llm=llm, registry=SkillRegistry(skills_dir),
                        memory=MemoryStore(self.tmp / "memory"), confirm=confirm)
        jarvis._use_planner = lambda: True  # pretend a cloud brain is present
        return jarvis

    def test_multistep_request_runs_the_agent_loop(self):
        # understand -> 2-step act; then the agent decides each step from the last result and finishes.
        reading = '{"intent": "act", "steps": ["open google docs", "search the web for the hope document"]}'
        dec1 = '{"done": false, "next": "search the web for the hope document"}'
        done = '{"done": true, "answer": "Found the hope document for you."}'
        jarvis = self.make(FakeLLM([reading, dec1, done]))
        reply = jarvis.handle("open google docs on my chrome and find the hope document")
        self.assertEqual(reply.route, "agent")
        self.assertEqual(reply.text, "Found the hope document for you.")
        self.assertEqual(reply.skill, "web_search")  # it actually ran the step it chose

    def test_agent_adapts_and_stops_when_it_repeats_itself(self):
        # A model that keeps proposing the same step must not loop forever - the guard breaks it.
        reading = '{"intent": "act", "steps": ["check the time", "do it again"]}'
        same = '{"done": false, "next": "what time is it"}'
        jarvis = self.make(FakeLLM([reading, same, same]))  # second identical decision -> loop guard
        reply = jarvis.handle("check the time twice")
        self.assertEqual(reply.route, "agent")
        self.assertEqual(reply.skill, "current_time")  # the one step it did run

    def test_agent_respects_max_steps(self):
        os.environ["JARVIS_AGENT__MAX_STEPS"] = "2"
        reading = '{"intent": "act", "steps": ["a", "b", "c"]}'
        keep_going = ['{"done": false, "next": "what time is it"}',
                      '{"done": false, "next": "search the web for cats"}']
        jarvis = self.make(FakeLLM([reading, *keep_going]))
        self.assertEqual(jarvis._agent_max_steps, 2)
        reply = jarvis.handle("do a lot of things")  # would run forever without the cap
        self.assertEqual(reply.route, "agent")  # stopped after 2 steps, no third decision requested

    def test_autonomy_skips_plan_review_and_per_action_prompts(self):
        reading = '{"intent": "act", "steps": ["search the web for cats", "search the web for dogs"]}'
        dec1 = '{"done": false, "next": "search the web for cats"}'
        done = '{"done": true, "answer": "All done."}'
        calls = []
        jarvis = self.make(FakeLLM([reading, dec1, done]), confirm=lambda req: calls.append(req) or "deny")
        jarvis.permissions.set_autonomy(True)  # unleashed
        with mock.patch("core.actions.webbrowser.open"), mock.patch("core.actions.subprocess.Popen"):
            reply = jarvis.handle("search cats and dogs on the web")
        self.assertEqual((reply.route, reply.text), ("agent", "All done."))
        self.assertEqual(calls, [])  # no plan approval, no per-action prompt

    def test_complex_plan_can_be_reviewed_and_cancelled(self):
        reading = '{"intent": "act", "steps": ["open google docs", "open gmail"]}'
        jarvis = self.make(FakeLLM([reading]), confirm=lambda req: "deny")  # reject the plan at review
        with mock.patch("core.actions.webbrowser.open") as browser:
            reply = jarvis.handle("open google docs and gmail")
            browser.assert_not_called()  # nothing ran
        self.assertEqual(reply.route, "cancelled")

    def test_understand_chat_intent(self):
        jarvis = self.make(FakeLLM(['{"intent": "chat", "reply": "A fine name is Sunny."}']))
        reply = jarvis.handle("what should i name my dog")
        self.assertEqual((reply.route, reply.text), ("chat", "A fine name is Sunny."))

    def test_understand_preference_switches_model_order_live(self):
        pref = '{"intent": "preference", "setting": "llm.fallback_order", "value": ["gemini", "groq", "ollama"], "summary": "Use Gemini first"}'
        jarvis = self.make(FakeLLM([pref]))
        reply = jarvis.handle("from now on use gemini as the main model")
        self.assertEqual(reply.route, "preference")
        self.assertEqual(jarvis.llm.order(), ["gemini", "groq", "ollama"])  # applied live
        self.assertEqual(load_settings().get("llm.fallback_order"), ["gemini", "groq", "ollama"])  # persisted

    def test_planner_off_when_only_local_model(self):
        from core.llm_router import LLMRouter
        jarvis = self.make(FakeLLM())
        jarvis._use_planner = Jarvis._use_planner.__get__(jarvis)  # restore the real check
        jarvis.llm = LLMRouter(load_settings())  # no keys -> only ollama could answer
        self.assertFalse(jarvis._use_planner())


class SkillsManagerTests(IsolatedCase):
    def test_meta_reader_and_disable_hides_from_loader(self):
        from ui.panel import _skill_meta_from_file

        skills_dir = self.tmp / "skills"
        skills_dir.mkdir()
        path = skills_dir / "greet.py"
        path.write_text("SKILL = {'name': 'greet', 'description': 'says hi', 'triggers': ['hello'], "
                        "'origin': 'evolved'}\ndef run(request, context):\n    return 'hi'\n", encoding="utf-8")
        meta = _skill_meta_from_file(path)
        self.assertEqual((meta["name"], meta["origin"]), ("greet", "evolved"))

        registry = SkillRegistry(skills_dir)
        registry.reload()
        self.assertIn("greet", registry.skills)
        os.replace(str(path), str(path) + ".disabled")  # how the manager disables a skill
        registry.reload()
        self.assertNotIn("greet", registry.skills)  # disabled files are ignored by the loader


class InterruptTests(IsolatedCase):
    def test_interrupt_stops_the_agent_loop(self):
        skills_dir = self.tmp / "skills"
        skills_dir.mkdir()
        for name in ("current_time", "web_search"):
            shutil.copy(SKILLS_DIR / f"{name}.py", skills_dir)

        holder = {}

        class InterruptingLLM:
            def __init__(self):
                self.n = 0

            def order(self):
                return ["fake"]

            def complete(self, prompt=None, **kwargs):
                self.n += 1
                if self.n == 1:  # understand -> a two-step act
                    return '{"intent": "act", "steps": ["what time is it", "search the web for cats"]}'
                holder["jarvis"].interrupt()  # user hits Ctrl+Alt+C mid-run
                return '{"done": false, "next": "what time is it"}'

        jarvis = Jarvis(load_settings(), llm=InterruptingLLM(), registry=SkillRegistry(skills_dir),
                        memory=MemoryStore(self.tmp / "memory"), confirm=lambda req: "once")
        jarvis._use_planner = lambda: True
        holder["jarvis"] = jarvis
        reply = jarvis.handle("what time is it and search the web for cats")
        self.assertEqual((reply.route, reply.text), ("interrupted", "Stopped."))

    def test_handle_clears_a_stale_interrupt(self):
        skills_dir = self.tmp / "skills"
        skills_dir.mkdir()
        shutil.copy(SKILLS_DIR / "current_time.py", skills_dir)
        jarvis = Jarvis(load_settings(), llm=FakeLLM(), registry=SkillRegistry(skills_dir),
                        memory=MemoryStore(self.tmp / "memory"))
        jarvis.interrupt()
        self.assertTrue(jarvis._cancelled())
        reply = jarvis.handle("thanks")  # a fresh request clears the old interrupt and answers normally
        self.assertFalse(jarvis._cancelled())
        self.assertEqual(reply.route, "chat")


class InterpreterTests(unittest.TestCase):
    def test_strip_politeness(self):
        from core.interpreter import normalize_command, strip_politeness
        self.assertEqual(strip_politeness("can you open notepad"), "open notepad")
        self.assertEqual(strip_politeness("hey jarvis, take a screenshot"), "take a screenshot")
        self.assertEqual(strip_politeness("please could you say hi"), "say hi")
        self.assertEqual(normalize_command("no i meant please open chrome"), ("open chrome", True))

    def test_is_clearly_chat(self):
        from core.interpreter import is_clearly_chat
        self.assertTrue(is_clearly_chat("what is your name"))
        self.assertTrue(is_clearly_chat("how are you"))
        self.assertTrue(is_clearly_chat("is it going to rain?"))
        self.assertFalse(is_clearly_chat("open notepad"))
        self.assertFalse(is_clearly_chat("read this aloud: hi"))


if __name__ == "__main__":
    unittest.main()
