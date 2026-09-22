"""Runs inside the sandbox process (python -I). Imports nothing from JARVIS except two
dependency-free modules loaded by path: the contract checker and the action broker.

The broker is given to the candidate skill in dry-run mode, so a skill that opens apps, takes
screenshots or touches files can be verified without doing any of it.

argv: candidate_skill.py cases.json contract.py actions.py
"""
import builtins
import contextlib
import importlib.util
import io
import json
import re
import sys
import traceback

MARKER = "__JARVIS_SANDBOX_REPORT__"


def report(**data):
    sys.__stdout__.write(MARKER + json.dumps(data) + "\n")
    sys.__stdout__.flush()


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def tb():
    return traceback.format_exc(limit=6)[-3000:]


def no_input(*_args, **_kwargs):
    raise RuntimeError("input() is not allowed in JARVIS skills; parse the request text instead")


def main():
    candidate_path, cases_path, contract_path, actions_path = sys.argv[1:5]
    with open(cases_path, encoding="utf-8") as fh:
        cases = json.load(fh)
    builtins.input = no_input
    contract = load(contract_path, "jarvis_contract")
    actions_mod = load(actions_path, "jarvis_actions")
    noise = io.StringIO()

    try:
        with contextlib.redirect_stdout(noise):
            module = load(candidate_path, "candidate_skill")
    except BaseException:
        return report(ok=False, stage="import", error=tb())

    problems = contract.contract_problems(module)
    if not problems:
        meta = module.SKILL
        if meta.get("name") != cases["expected_name"]:
            problems.append(f"SKILL['name'] must be {cases['expected_name']!r}, got {meta.get('name')!r}")
        if not any(re.search(t, cases["must_match"], re.IGNORECASE) for t in meta["triggers"]):
            problems.append(f"none of the triggers {meta['triggers']!r} match the original request {cases['must_match']!r}")
    if problems:
        return report(ok=False, stage="contract", error="\n".join(problems))

    outputs = []
    for text in cases["inputs"]:
        context = {
            "dry_run": True,
            "actions": actions_mod.ActionBroker(dry_run=True),
            "llm": lambda prompt, **_kw: "[LLM unavailable during sandbox verification]",
            "run": lambda *_a, **_kw: "[sub-skill unavailable during sandbox verification]",
            "memory": [],
            "platform": {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux"),
            "skills": [],
            "emit": lambda *_a, **_kw: None,
        }
        try:
            with contextlib.redirect_stdout(noise):
                result = module.run(text, context)
        except BaseException:
            return report(ok=False, stage="run", input=text, error=tb(), outputs=outputs)
        if not isinstance(result, str) or not result.strip():
            return report(ok=False, stage="run", input=text, outputs=outputs,
                          error=f"run() must return a non-empty str, got {type(result).__name__}: {result!r}"[:500])
        outputs.append({"input": text, "output": result[:500]})

    report(ok=True, stage="done", outputs=outputs)


if __name__ == "__main__":
    main()
