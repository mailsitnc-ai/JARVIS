import ast
import json
import os
import shutil
import subprocess
import unittest

from core.config import SKILLS_DIR, load_settings
from core.orchestrator import Jarvis
from core.skill_loader import SkillRegistry
from evolution_engine.analyzer import clean_triggers, heuristic_spec, is_meta_request, normalize_spec
from evolution_engine.prompts import DRAFT_EXAMPLES
from unittest import mock

from evolution_engine.analyzer import CapabilitySpec
from evolution_engine.engine import EvolutionEngine
from evolution_engine.sandbox import (SandboxResult, declared_requires, ensure_entry_point, enforce_skill_meta,
                                       missing_module, run_sandbox, static_check, tidy_top_level)
from memory.store import MemoryStore
from tests.helpers import FakeLLM, IsolatedCase

GOOD_SKILL = '''```python
import re

SKILL = {"name": "vowel_counter", "description": "Count vowels in text.", "triggers": ["\\\\bvowels?\\\\b"], "version": 1, "origin": "evolved"}


def run(request, context):
    match = re.search(r"\\bin\\s+(.+)$", request, re.IGNORECASE)
    text = match.group(1) if match else request
    count = sum(1 for ch in text.lower() if ch in "aeiou")
    return f"{text} has {count} vowels."
```'''

ANALYSIS = json.dumps({
    "kind": "skill", "name": "vowel_counter", "description": "Count vowels in text.",
    "triggers": ["\\bvowels?\\b"], "test_inputs": ["how many vowels in banana"], "side_effects": False,
    "plan": "Find the word after 'in' and count a, e, i, o, u.",
})


def skill_source(body, name="probe", triggers=("probe",), origin="evolved"):
    return (f"SKILL = {{'name': {name!r}, 'description': 'd', 'triggers': {list(triggers)!r}, 'origin': {origin!r}}}\n\n"
            f"def run(request, context):\n{body}\n")


class EvolutionLoopTests(IsolatedCase):
    def make(self, llm):
        self.skills_dir = self.tmp / "skills"
        return Jarvis(load_settings(), llm=llm, registry=SkillRegistry(self.skills_dir),
                      memory=MemoryStore(self.tmp / "memory"), archive_dir=self.tmp / "archive")

    def test_missing_skill_is_drafted_verified_installed_and_used(self):
        llm = FakeLLM([ANALYSIS, "```python\ndef run(:\n```", GOOD_SKILL])
        jarvis = self.make(llm)

        reply = jarvis.handle("count the vowels in hello")
        self.assertEqual(reply.route, "evolved")
        self.assertEqual(reply.text, "hello has 2 vowels.")
        self.assertIn("SyntaxError", llm.calls[2], "the retry prompt must carry the verification error")
        roles = [m["role"] for m in llm.kwargs[1]["messages"]]
        self.assertEqual(roles[0], "system")
        self.assertEqual(roles[-1], "user")
        self.assertIn("assistant", roles, "drafts must carry worked example turns")
        self.assertIn("json_schema", llm.kwargs[0])
        self.assertGreater(llm.kwargs[2]["temperature"], llm.kwargs[1]["temperature"], "retries must sample warmer")
        self.assertTrue((self.skills_dir / "vowel_counter.py").exists())

        again = jarvis.handle("how many vowels in banana")  # hot-reloaded: no LLM calls left
        self.assertEqual((again.route, again.text), ("trigger", "banana has 3 vowels."))
        events = [e["event"] for e in jarvis.memory.evolution_events()]
        self.assertEqual(events, ["analyzed", "rejected", "installed"])

    def test_submit_returns_a_background_job_for_builds(self):
        llm = FakeLLM([ANALYSIS, GOOD_SKILL])
        jarvis = self.make(llm)
        ack, job = jarvis.submit("count the vowels in hello")
        self.assertEqual(ack.route, "building")
        self.assertIsNotNone(job)
        self.assertEqual(llm.calls, [])  # nothing built during the quick phase
        result = job()
        self.assertEqual((result.route, result.text), ("evolved", "hello has 2 vowels."))

    def test_submit_answers_fast_paths_inline(self):
        reply, job = self.make(FakeLLM()).submit("thanks")  # small talk: no model, no job
        self.assertIsNone(job)
        self.assertEqual(reply.route, "chat")

    def test_gives_up_after_max_attempts_without_installing(self):
        os.environ["JARVIS_EVOLUTION__MAX_ATTEMPTS"] = "2"
        crashing = "```python\n" + skill_source("    return str(1 / 0)", "vowel_counter", ["vowel"]) + "```"
        jarvis = self.make(FakeLLM([ANALYSIS, crashing, crashing]))
        reply = jarvis.handle("count the vowels in hello")
        self.assertEqual(reply.route, "evolution-failed")
        self.assertIn("ZeroDivisionError", reply.text)
        self.assertEqual(list(self.skills_dir.glob("*.py")), [])
        self.assertTrue(jarvis.learning.recent_lessons(kinds=("build_failed",)))  # the failure was recorded

    def test_knowledge_questions_are_chatted_not_turned_into_skills(self):
        llm = FakeLLM(["Paris."])  # a question with no action verb -> one chat reply, no analysis
        jarvis = self.make(llm)
        reply = jarvis.handle("what is the capital of France")
        self.assertEqual((reply.route, reply.text), ("chat", "Paris."))
        self.assertEqual(len(llm.calls), 1)

    def test_greetings_are_chatted_without_analysis(self):
        llm = FakeLLM(["Hello! How can I help?"])
        jarvis = self.make(llm)
        reply = jarvis.handle("hello there")
        self.assertEqual((reply.route, reply.text), ("chat", "Hello! How can I help?"))
        self.assertEqual(len(llm.calls), 1)

    def test_self_code_edits_are_gated_not_silently_declined(self):
        # Self-editing is now a real capability, but gated: without permission it's refused, nothing runs.
        self.assertFalse(is_meta_request("give yourself the ability to speak"))  # gaining abilities is fine
        self.assertFalse(is_meta_request("read this text aloud"))
        llm = FakeLLM()  # no responses: a model call would raise
        reply = self.make(llm).handle("edit your own source code")  # confirm is None -> denied
        self.assertEqual(reply.route, "denied")
        self.assertIn("permission", reply.text.lower())
        self.assertEqual(llm.calls, [])
        self.assertEqual(list(self.skills_dir.glob("*.py")), [])  # nothing built or edited

    def test_duplicate_capability_is_reused_not_rebuilt(self):
        self.skills_dir = self.tmp / "skills"
        self.skills_dir.mkdir()
        shutil.copy(SKILLS_DIR / "web_search.py", self.skills_dir)
        analysis = json.dumps({"kind": "skill", "name": "web_search_2",
                               "description": "Search the web for something in the default browser.",
                               "triggers": ["\\bfind\\b"], "test_inputs": ["find cats"], "plan": "open a search url"})
        jarvis = Jarvis(load_settings(), llm=FakeLLM([analysis]), registry=SkillRegistry(self.skills_dir),
                        memory=MemoryStore(self.tmp / "memory"), archive_dir=self.tmp / "archive",
                        confirm=lambda req: "deny")
        reply = jarvis.handle("find cats using the internet")
        self.assertEqual(reply.route, "reused")
        self.assertEqual(reply.skill, "web_search")
        self.assertEqual([p.name for p in self.skills_dir.glob("*.py")], ["web_search.py"])  # no clone written
        self.assertIn("web_search", jarvis.learning.hinted_skills("find cats using the internet"))  # learned the route

    def test_failure_output_pattern(self):
        from core.orchestrator import _FAILURE_OUTPUT
        self.assertTrue(_FAILURE_OUTPUT.search("could not be imported"))
        self.assertTrue(_FAILURE_OUTPUT.search("NameError: foo is not defined"))
        self.assertTrue(_FAILURE_OUTPUT.search("Failed to convert the image"))
        self.assertFalse(_FAILURE_OUTPUT.search("I couldn't find an app called foo"))  # legit answer
        self.assertFalse(_FAILURE_OUTPUT.search("No results for your search."))

    def test_evolved_skill_that_returns_an_error_is_repaired(self):
        self.skills_dir = self.tmp / "skills"
        self.skills_dir.mkdir()
        broken = skill_source("    return 'Traceback (most recent call last): NameError: foo is not defined'",
                              "gadget", [r"\bgadget\b"])
        (self.skills_dir / "gadget.py").write_text(broken, encoding="utf-8")
        fixed = "```python\n" + skill_source("    return 'gadget ready'", "gadget", [r"\bgadget\b"]) + "```"
        jarvis = self.make(FakeLLM([fixed]))
        reply = jarvis.handle("gadget")
        self.assertEqual(reply.text, "gadget ready")  # observed the swallowed error, rebuilt, re-ran

    def test_improve_target_detection(self):
        self.skills_dir = self.tmp / "skills"
        self.skills_dir.mkdir()
        (self.skills_dir / "gadget.py").write_text(skill_source("    return 'x'", "gadget", [r"\bgadget\b"]),
                                                   encoding="utf-8")
        jarvis = self.make(FakeLLM())
        jarvis.registry.reload()
        self.assertIsNotNone(jarvis._improve_target("improve your gadget skill"))
        self.assertIsNotNone(jarvis._improve_target("make the gadget skill better"))
        self.assertIsNone(jarvis._improve_target("open the gadget"))     # no verb + "skill"
        self.assertIsNone(jarvis._improve_target("what's the weather"))  # unrelated

    def test_improve_rewrites_an_evolved_skill(self):
        self.skills_dir = self.tmp / "skills"
        self.skills_dir.mkdir()
        (self.skills_dir / "gadget.py").write_text(skill_source("    return 'gadget v1'", "gadget", [r"\bgadget\b"]),
                                                   encoding="utf-8")
        better = "```python\n" + skill_source("    return 'gadget v2 improved'", "gadget", [r"\bgadget\b"]) + "```"
        jarvis = self.make(FakeLLM([better]))
        reply = jarvis.handle("improve your gadget skill")
        self.assertEqual((reply.route, reply.skill), ("improved", "gadget"))
        self.assertEqual(jarvis.handle("gadget").text, "gadget v2 improved")  # the rewritten version is live
        self.assertEqual(len(list((self.tmp / "archive").glob("gadget.*.py"))), 1)  # old version archived

    def test_improve_refuses_builtin_skills(self):
        self.skills_dir = self.tmp / "skills"
        self.skills_dir.mkdir()
        shutil.copy(SKILLS_DIR / "calculator.py", self.skills_dir)
        jarvis = self.make(FakeLLM())  # no responses: drafting would raise
        jarvis.registry.reload()
        outcome = jarvis.evolution.improve(jarvis.registry.get("calculator"), reason="be better")
        self.assertEqual(outcome.kind, "failed")
        self.assertEqual(jarvis.llm.calls, [])  # refused before any model call

    def test_autonomy_lets_evolved_skills_use_risky_calls(self):
        from core.permissions import PermissionRegistry

        perms = PermissionRegistry(self.tmp / "p.json")
        jarvis = Jarvis(load_settings(), llm=FakeLLM(), registry=SkillRegistry(self.tmp / "skills"),
                        memory=MemoryStore(self.tmp / "memory"), permissions=perms)
        self.assertFalse(jarvis.evolution.allow_risky)  # gated by default
        perms.set_autonomy(True)
        self.assertTrue(jarvis.evolution.allow_risky)   # unleashed -> risky calls permitted

    def test_crashing_evolved_skill_repairs_itself(self):
        llm = FakeLLM()
        jarvis = self.make(llm)
        self.skills_dir.mkdir(exist_ok=True)
        broken = skill_source("    return str(1 / 0)", "word_length", [r"\blength of\b"])
        (self.skills_dir / "word_length.py").write_text(broken, encoding="utf-8")
        fixed = skill_source("    word = request.split()[-1]\n    return f'{len(word)} letters'", "word_length", [r"\blength of\b"])
        llm.responses.append("```python\n" + fixed + "```")

        reply = jarvis.handle("length of banana")
        self.assertEqual(reply.text, "6 letters")
        self.assertEqual(len(list((self.tmp / "archive").glob("word_length.*.py"))), 1)


class SelfEditTests(IsolatedCase):
    def _root(self):
        root = self.tmp / "proj"
        (root / "core").mkdir(parents=True)
        (root / "core" / "widget.py").write_text("VALUE = 1\n", encoding="utf-8")
        return root

    def _editor(self, llm, root, run_tests=False, git_commit=False):
        from evolution_engine.self_edit import SelfEditor
        return SelfEditor(llm, root, archive_dir=root / "_self_edits", run_tests=run_tests, git_commit=git_commit)

    def test_regex_matches_self_edit_commands_only(self):
        from core.orchestrator import _SELF_EDIT
        for yes in ("rewrite your own code to add X", "edit your source", "change your code so it logs",
                    "modify your orchestrator.py", "rewrite yourself", "add to your code a feature"):
            self.assertTrue(_SELF_EDIT.search(yes), yes)
        for no in ("improve your qr skill", "write a python script", "open notepad", "what is your name"):
            self.assertFalse(_SELF_EDIT.search(no), no)

    def test_edit_rewrites_a_file_and_backs_it_up(self):
        root = self._root()
        editor = self._editor(FakeLLM(["VALUE = 2\n"]), root)  # file named -> no pick call, one draft call
        result = editor.edit("change your core/widget.py so VALUE is 2")
        self.assertTrue(result.ok, result.message)
        self.assertEqual((root / "core" / "widget.py").read_text().strip(), "VALUE = 2")
        self.assertTrue(list((root / "_self_edits").glob("widget.py.*.py")))  # original archived

    def test_syntax_error_draft_leaves_file_unchanged(self):
        root = self._root()
        editor = self._editor(FakeLLM(["def broken(:\n"]), root)
        result = editor.edit("edit your core/widget.py")
        self.assertFalse(result.ok)
        self.assertIn("syntax", result.message.lower())
        self.assertEqual((root / "core" / "widget.py").read_text().strip(), "VALUE = 1")

    def test_failing_tests_roll_the_change_back(self):
        root = self._root()
        editor = self._editor(FakeLLM(["VALUE = 2\n"]), root, run_tests=True)
        with mock.patch.object(editor, "_run_tests", return_value=(False, "boom")):
            result = editor.edit("change your core/widget.py so VALUE is 2")
        self.assertFalse(result.ok)
        self.assertIn("rolled it back", result.message)
        self.assertEqual((root / "core" / "widget.py").read_text().strip(), "VALUE = 1")  # restored

    def test_protected_file_is_refused(self):
        root = self._root()
        (root / "core" / "keystore.py").write_text("SECRET = 1\n", encoding="utf-8")
        editor = self._editor(FakeLLM(), root)  # no responses: must refuse before drafting
        result = editor.edit("rewrite your core/keystore.py")
        self.assertFalse(result.ok)
        self.assertIn("protected", result.message.lower())

    @unittest.skipUnless(shutil.which("git"), "git not available")
    def test_successful_edit_is_committed_to_git(self):
        root = self._root()
        editor = self._editor(FakeLLM(["VALUE = 2\n"]), root, git_commit=True)
        result = editor.edit("change your core/widget.py so VALUE is 2")
        self.assertTrue(result.ok, result.message)
        self.assertIn("committed", result.message.lower())
        log = subprocess.run(["git", "-C", str(root), "log", "--oneline"], capture_output=True, text=True)
        self.assertIn("JARVIS self-edit", log.stdout)  # the edit is a revertible commit


class SelfEditRoutingTests(IsolatedCase):
    def _jarvis(self, allow=True):
        skills_dir = self.tmp / "skills"
        skills_dir.mkdir()
        jarvis = Jarvis(load_settings(), llm=FakeLLM(), registry=SkillRegistry(skills_dir),
                        memory=MemoryStore(self.tmp / "memory"), confirm=lambda req: "deny")
        if allow:
            jarvis.permissions.set("self_edit", "allow")
        return jarvis

    def test_self_edit_command_routes_to_the_editor(self):
        from evolution_engine.self_edit import SelfEditResult
        jarvis = self._jarvis(allow=True)
        jarvis._self_editor = mock.Mock()
        jarvis._self_editor.edit.return_value = SelfEditResult(True, "Done - rewrote core/x.py.", "core/x.py")
        reply = jarvis.handle("rewrite your own code to add a greeting")
        jarvis._self_editor.edit.assert_called_once()
        self.assertEqual((reply.route, reply.skill), ("self-edited", "core/x.py"))

    def test_self_edit_denied_without_permission(self):
        jarvis = self._jarvis(allow=False)  # self_edit stays 'ask'; confirm returns deny
        jarvis._self_editor = mock.Mock()
        reply = jarvis.handle("rewrite your own source code")
        jarvis._self_editor.edit.assert_not_called()
        self.assertEqual(reply.route, "denied")


class PackageTests(IsolatedCase):
    def test_declared_requires_and_missing_module(self):
        code = ("import bs4\nSKILL = {'name': 'x', 'description': 'd', 'triggers': ['x'], "
                "'requires': ['beautifulsoup4', 'lxml']}\n\ndef run(request, context):\n    return 'ok'\n")
        self.assertEqual(declared_requires(code), ["beautifulsoup4", "lxml"])
        self.assertEqual(missing_module("...\nModuleNotFoundError: No module named 'bs4'"), "bs4")
        self.assertIsNone(missing_module("some other error"))

    def test_enforce_meta_preserves_requires(self):
        code = ("SKILL = {'name': 'wrong', 'description': 'Parse HTML.', 'triggers': ['x'], "
                "'requires': ['beautifulsoup4']}\n\ndef run(request, context):\n    return 'ok'\n")
        fixed, _changed = enforce_skill_meta(code, "html_tool", "fallback", [r"\bhtml\b"])
        meta = ast.literal_eval(fixed.split("SKILL = ", 1)[1].splitlines()[0])
        self.assertEqual(meta["requires"], ["beautifulsoup4"])

    def test_verify_installs_a_missing_module_then_reverifies(self):
        installed = []
        engine = EvolutionEngine(FakeLLM(), SkillRegistry(self.tmp / "skills"), load_settings(),
                                 installer=lambda pkg: installed.append(pkg) or f"Installed {pkg}.")
        spec = CapabilitySpec(kind="skill", name="html_tool", description="d", triggers=["html"], test_inputs=["x"])
        results = [SandboxResult(False, "run stage: ModuleNotFoundError: No module named 'bs4'"),
                   SandboxResult(True, outputs=[{"input": "x", "output": "ok"}])]
        with mock.patch("evolution_engine.engine.run_sandbox", side_effect=results):
            result = engine._verify("import bs4\n", spec, "x")
        self.assertTrue(result.ok)
        self.assertEqual(installed, ["beautifulsoup4"])  # mapped bs4 -> beautifulsoup4 and installed


class SandboxTests(IsolatedCase):
    def check(self, body, **kwargs):
        return run_sandbox(skill_source(body), expected_name="probe", must_match="probe this",
                           inputs=["probe this"], timeout_s=kwargs.get("timeout_s", 20))

    def test_good_skill_passes(self):
        result = self.check("    return 'fine'")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.outputs[0]["output"], "fine")

    def test_runtime_error_fails_with_traceback(self):
        result = self.check("    return {}['missing']")
        self.assertFalse(result.ok)
        self.assertIn("KeyError", result.error)

    def test_empty_or_non_string_result_fails(self):
        self.assertFalse(self.check("    return 42").ok)

    def test_infinite_loop_times_out(self):
        result = self.check("    while True:\n        pass", timeout_s=3)
        self.assertFalse(result.ok)
        self.assertIn("timed out", result.error)

    def test_api_keys_do_not_reach_the_sandbox(self):
        os.environ["GROQ_API_KEY"] = "super-secret"
        result = self.check("    import os\n    return os.environ.get('GROQ_API_KEY', 'no key visible')")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.outputs[0]["output"], "no key visible")

    def test_trigger_must_match_the_original_request(self):
        code = skill_source("    return 'x'", triggers=["unrelated"])
        result = run_sandbox(code, expected_name="probe", must_match="probe this", inputs=["probe this"])
        self.assertFalse(result.ok)
        self.assertIn("match the original request", result.error)

    def test_tidy_removes_example_usage_but_keeps_definitions(self):
        code = (skill_source("    return 'x'") + "\nresult = run('probe', {})\nprint(result)\n"
                "for i in range(3):\n    print(i)\nVOWELS = set('aeiou')\n")
        tidied, removed = tidy_top_level(code)
        self.assertEqual(removed, 4)
        self.assertEqual(static_check(tidied), [])
        self.assertIn("VOWELS = set('aeiou')", tidied)
        self.assertIn("def run", tidied)
        self.assertEqual(tidy_top_level("def run(:"), ("def run(:", 0))

    def test_static_check_blocks_risky_and_blocking_code(self):
        self.assertIn("SyntaxError", static_check("def run(:")[0])
        risky = skill_source("    import shutil\n    shutil.rmtree('C:/x')\n    return 'x'")
        self.assertTrue(any("rmtree" in p for p in static_check(risky)))
        self.assertEqual(static_check(risky, allow_risky=True), [])
        self.assertTrue(any("input()" in p for p in static_check(skill_source("    return input()"), allow_risky=True)))
        self.assertTrue(any("runs on import" in p for p in static_check(skill_source("    return 'x'") + "print('hi')\n")))


class SmallModelHardeningTests(IsolatedCase):
    """Failure modes measured with qwen2.5-coder:0.5b on this laptop."""

    def test_placeholders_value_triggers_and_junk_inputs_are_cleaned(self):
        data = {"kind": "skill", "name": "snake_case_skill_name", "description": "Count vowels",
                "triggers": ["regex", "banana", "\\bvowels\\b", ".*"],
                "test_inputs": ["request", "banana", "how many vowels in apple"], "plan": ""}
        spec = normalize_spec(data, "count the vowels in the word banana")
        self.assertEqual(spec.name, "count_vowels")
        self.assertEqual(spec.triggers, ["\\bvowels\\b"])
        self.assertEqual(spec.test_inputs, ["count the vowels in the word banana", "how many vowels in apple"])
        self.assertTrue(spec.plan)

    def test_computations_labelled_answer_become_skills(self):
        answer = {"kind": "answer", "name": "x", "description": "d", "triggers": [], "test_inputs": [], "plan": ""}
        self.assertEqual(normalize_spec(answer, "count the vowels in the word banana").kind, "skill")
        self.assertEqual(normalize_spec(answer, "how many vowels are in banana").kind, "skill")
        self.assertEqual(normalize_spec(answer, "what is 15% of 80 converted to euros").kind, "skill")
        self.assertEqual(normalize_spec(answer, "who wrote hamlet").kind, "answer")
        self.assertEqual(normalize_spec(answer, "what is the capital of France").kind, "answer")
        self.assertEqual(normalize_spec(answer, "what movie won best picture in 1998").kind, "answer")
        self.assertEqual(normalize_spec(answer, "hello").kind, "answer")
        # A greeting the model mislabels "skill" is still answered, never turned into code.
        skill_guess = {"kind": "skill", "name": "x", "description": "d", "triggers": [], "test_inputs": [], "plan": ""}
        self.assertEqual(normalize_spec(skill_guess, "hello there").kind, "answer")
        self.assertEqual(normalize_spec(answer, "take a screenshot of my screen").kind, "skill")
        self.assertEqual(normalize_spec(answer, "open a new tab on my chrome").kind, "skill")

    def test_generic_verb_triggers_are_dropped(self):
        request = "generate a random password with 12 characters"
        self.assertEqual(clean_triggers(["\\bgenerate\\b", "\\bpassword\\b"], request), ["\\bpassword\\b"])
        self.assertEqual(clean_triggers(["\\bgenerate\\b"], request), [r"\bpassword\b"])
        self.assertEqual(heuristic_spec(request).triggers, [r"\bpassword\b"])

    def test_heuristic_spec_names_the_capability_not_the_value(self):
        spec = heuristic_spec("count the vowels in the word banana")
        self.assertEqual((spec.name, spec.triggers), ("count_vowels", [r"\bvowels\b"]))

    def test_skill_meta_is_enforced_or_inserted(self):
        code = ("import re\n\nSKILL = {'name': 'wrong', 'description': 'Counts vowels.', 'triggers': ['x']}\n\n"
                "def run(request, context):\n    return 'ok'\n")
        fixed, changed = enforce_skill_meta(code, "count_vowels", "fallback", [r"\bvowels\b"])
        meta = ast.literal_eval(fixed.split("SKILL = ", 1)[1].splitlines()[0])
        self.assertTrue(changed)
        self.assertEqual((meta["name"], meta["triggers"], meta["description"]), ("count_vowels", [r"\bvowels\b"], "Counts vowels."))

        inserted, changed = enforce_skill_meta("import re\n\ndef run(request, context):\n    return 'ok'\n",
                                               "count_vowels", "Count vowels.", [r"\bvowels\b"])
        self.assertTrue(changed)
        self.assertEqual(static_check(inserted), [])

    def test_missing_run_entry_point_is_added(self):
        code = ("import re\n\nSKILL = {'name': 'count_vowels', 'description': 'd', 'triggers': ['vowels']}\n\n"
                "def count_vowels(text):\n    return f\"{sum(c in 'aeiou' for c in text.split()[-1])} vowels\"\n")
        fixed, wrapped = ensure_entry_point(code)
        self.assertTrue(wrapped)
        self.assertEqual(static_check(fixed), [])
        result = run_sandbox(fixed, expected_name="count_vowels", must_match="count the vowels in banana",
                             inputs=["count the vowels in banana"])
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.outputs[0]["output"], "3 vowels")
        self.assertEqual(ensure_entry_point(fixed), (fixed, False))

    def test_worked_draft_examples_are_themselves_valid_skills(self):
        for example in DRAFT_EXAMPLES:
            self.assertEqual(static_check(example["code"]), [], example["name"])
            result = run_sandbox(example["code"], expected_name=example["name"], must_match=example["request"],
                                 inputs=[example["request"]])
            self.assertTrue(result.ok, f"{example['name']}: {result.error}")

    def test_reverse_example_reverses(self):
        code = DRAFT_EXAMPLES[0]["code"]
        result = run_sandbox(code, expected_name="reverse_text", must_match="reverse hello",
                             inputs=["reverse the word python", "write 'stressed' backwards"])
        self.assertEqual([o["output"] for o in result.outputs],
                         ["'python' reversed is 'nohtyp'.", "'stressed' reversed is 'desserts'."])

    def test_action_example_verifies_without_touching_the_system(self):
        # The launch_app example calls context["actions"].open_app; in the sandbox it must simulate.
        result = run_sandbox(DRAFT_EXAMPLES[1]["code"], expected_name="launch_app", must_match="launch notepad",
                             inputs=["launch notepad"])
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.outputs[0]["output"], "Would open Notepad.")

    def test_static_check_blocks_raw_side_effects_and_points_to_the_broker(self):
        for snippet in ("    import subprocess\n    return subprocess.run(['x'])",
                        "    import webbrowser\n    return webbrowser.open('x')",
                        "    import os\n    os.startfile('x')\n    return 'x'",
                        "    import urllib.request\n    return urllib.request.urlopen('x').read()",
                        "    import socket\n    return str(socket.socket())"):
            problems = static_check(skill_source(snippet))
            self.assertTrue(any('context["actions"]' in p for p in problems), snippet)


if __name__ == "__main__":
    unittest.main()
