"""Step 3 of evolution: verify a drafted skill before it is allowed into /skills.

Two gates:
  static_check  parses the code and rejects syntax errors, contract gaps, code that runs on
                import, and risky calls (file deletion, eval/exec, ctypes, shell=True, input()).
  run_sandbox   runs the skill in a separate, isolated Python process: its own temp directory,
                a scrubbed environment with no API keys, `python -I`, a Windows Job Object memory
                cap, and a hard timeout. It imports the module, checks the contract, then calls
                run() on every test input with dry_run=True.

This is a safety net against broken or careless drafts, not a security boundary against
hostile code.
"""
from __future__ import annotations

import ast
import ctypes
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = HERE / "sandbox_harness.py"
CONTRACT = HERE.parent / "core" / "contract.py"
ACTIONS = HERE.parent / "core" / "actions.py"
MARKER = "__JARVIS_SANDBOX_REPORT__"

RISKY_NAMES = {"eval", "exec", "__import__", "breakpoint"}
RISKY_ATTRS = {"remove", "unlink", "rmdir", "removedirs", "rmtree", "system", "popen",
               "DeleteKey", "DeleteKeyEx", "DeleteValue", "SetValue", "SetValueEx"}
RISKY_MODULES = {"ctypes", "_ctypes", "_winapi"}
# Side effects (including network) must go through context["actions"] so every one is gated.
BROKERED_MODULES = {"subprocess", "webbrowser", "urllib", "socket", "http", "requests", "httpx", "ftplib"}
BROKERED_ATTRS = {"startfile"}
_BROKER_HINT = ('use context["actions"] (open_app, open_url, screenshot, read_file, write_file, '
                'run_command, http_request) instead')
ALLOWED_TOP_LEVEL = (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign, ast.FunctionDef,
                     ast.ClassDef, ast.If, ast.Try)


@dataclass
class SandboxResult:
    ok: bool
    error: str = ""
    outputs: list = field(default_factory=list)
    duration_s: float = 0.0


def static_check(code: str, allow_risky: bool = False) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"SyntaxError on line {exc.lineno}: {exc.msg}"]

    problems: list[str] = []
    assigned = {t.id for node in tree.body if isinstance(node, ast.Assign) for t in node.targets if isinstance(t, ast.Name)}
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    if "SKILL" not in assigned:
        problems.append("no top-level SKILL = {...} dict")
    if "run" not in functions:
        problems.append("no top-level def run(request, context)")

    for node in tree.body:
        is_docstring = isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        if not isinstance(node, ALLOWED_TOP_LEVEL) and not is_docstring:
            problems.append(f"line {node.lineno}: top-level statement runs on import; move it into a function")

    risky: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "input":
                problems.append(f"line {node.lineno}: input() would freeze JARVIS; parse the request instead")
            elif isinstance(func, ast.Name) and func.id in RISKY_NAMES:
                risky.append(f"line {node.lineno}: {func.id}() is not allowed")
            elif isinstance(func, ast.Attribute) and func.attr in BROKERED_ATTRS:
                risky.append(f"line {node.lineno}: .{func.attr}() is not allowed; {_BROKER_HINT}")
            elif isinstance(func, ast.Attribute) and func.attr in RISKY_ATTRS:
                risky.append(f"line {node.lineno}: .{func.attr}() is not allowed (destructive or shell call)")
            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value:
                    risky.append(f"line {node.lineno}: shell=True is not allowed; pass an argument list")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in RISKY_MODULES:
                    risky.append(f"line {node.lineno}: importing {alias.name} is not allowed")
                elif root in BROKERED_MODULES:
                    risky.append(f"line {node.lineno}: don't import {alias.name}; {_BROKER_HINT}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in RISKY_MODULES:
                risky.append(f"line {node.lineno}: importing {node.module} is not allowed")
            elif root in BROKERED_MODULES:
                risky.append(f"line {node.lineno}: don't import {node.module}; {_BROKER_HINT}")

    if not allow_risky:
        problems.extend(risky)
    return list(dict.fromkeys(problems))


# module name -> pip package name, where they differ
MODULE_TO_PIP = {"bs4": "beautifulsoup4", "cv2": "opencv-python", "PIL": "pillow", "yaml": "pyyaml",
                 "dateutil": "python-dateutil", "sklearn": "scikit-learn", "dotenv": "python-dotenv",
                 "docx": "python-docx", "fitz": "pymupdf", "serial": "pyserial"}


def declared_requires(code: str) -> list[str]:
    """The pip packages a skill declares in SKILL['requires']."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SKILL" for t in node.targets):
            if isinstance(node.value, ast.Dict):
                for key, value in zip(node.value.keys, node.value.values):
                    if isinstance(key, ast.Constant) and key.value == "requires" and isinstance(value, (ast.List, ast.Tuple)):
                        return [e.value.strip() for e in value.elts
                                if isinstance(e, ast.Constant) and isinstance(e.value, str) and e.value.strip()]
    return []


def missing_module(error: str) -> str | None:
    """The top-level module name from a 'No module named X' error, or None."""
    match = re.search(r"No module named '([\w.]+)'", error or "")
    return match.group(1).split(".")[0] if match else None


def _calls_any(node: ast.AST, names: set[str]) -> bool:
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in names for n in ast.walk(node))


def tidy_top_level(code: str) -> tuple[str, int]:
    """Drop example usage a model tacks on at module level: print(run(...)), demo loops, stray calls.

    Small local models add these out of habit and repeat the habit when told not to, so removing
    them is faster and more reliable than another draft. Returns (code, number of lines removed).
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code, 0
    defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    defined |= {"print", "input"}
    drop: list[tuple[int, int]] = []
    for node in tree.body:
        is_docstring = isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        if isinstance(node, ALLOWED_TOP_LEVEL) or is_docstring:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None and _calls_any(node.value, defined):
                drop.append((node.lineno, node.end_lineno))  # e.g. result = run("banana", {})
            continue
        drop.append((node.lineno, node.end_lineno))
    if not drop:
        return code, 0
    lines = code.splitlines(keepends=True)
    removed = 0
    for start, end in reversed(drop):
        del lines[start - 1:end]
        removed += end - start + 1
    return "".join(lines).rstrip() + "\n", removed


def ensure_entry_point(code: str) -> tuple[str, bool]:
    """Add run(request, context) when a model named its main function something else, like count_vowels(text).

    Models usually write helpers first and the main function last, so the last function is wrapped.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code, False
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    if not functions or any(f.name == "run" for f in functions):
        return code, False
    main = functions[-1]
    positional = len(main.args.posonlyargs) + len(main.args.args)
    arguments = "request, context" if positional >= 2 else ("request" if positional == 1 or main.args.vararg else "")
    wrapper = f"\n\ndef run(request, context):\n    return str({main.name}({arguments}))\n"
    return code.rstrip() + "\n" + wrapper, True


def enforce_skill_meta(code: str, name: str, description: str, triggers: list[str]) -> tuple[str, bool]:
    """Write the analyzed name and triggers into the SKILL dict, adding it if missing.

    Small models rename skills or mangle regex escapes; fixing that mechanically saves a whole
    redraft. The model's own description is kept when it wrote one. Returns (code, changed).
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code, False
    meta = {"name": name, "description": description, "triggers": list(triggers), "version": 1, "origin": "evolved"}
    lines = code.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SKILL" for t in node.targets):
            if isinstance(node.value, ast.Dict):
                for key, value in zip(node.value.keys, node.value.values):
                    if not isinstance(key, ast.Constant):
                        continue
                    if key.value == "description" and isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value.strip():
                        meta["description"] = value.value.strip()
                    elif key.value == "requires" and isinstance(value, (ast.List, ast.Tuple)):  # keep declared packages
                        pkgs = [e.value for e in value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str) and e.value.strip()]
                        if pkgs:
                            meta["requires"] = pkgs
            replacement = f"SKILL = {meta!r}\n"
            if "".join(lines[node.lineno - 1:node.end_lineno]).strip() == replacement.strip():
                return code, False
            lines[node.lineno - 1:node.end_lineno] = [replacement]
            return "".join(lines), True

    insert_at = 0
    for node in tree.body:
        is_docstring = isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        if isinstance(node, (ast.Import, ast.ImportFrom)) or is_docstring:
            insert_at = node.end_lineno
        else:
            break
    lines[insert_at:insert_at] = ["\n", f"SKILL = {meta!r}\n", "\n"]
    return "".join(lines), True


def python_executable() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        return str(exe.with_name("python.exe"))
    return str(exe)


def _sandbox_env(tmpdir: str) -> dict:
    # APPDATA/LOCALAPPDATA are kept so pip-installed user-site packages resolve; API keys are not (below).
    keep = ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS",
            "PROCESSOR_ARCHITECTURE", "OS", "USERPROFILE", "USERNAME", "HOMEDRIVE", "HOMEPATH",
            "APPDATA", "LOCALAPPDATA")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    root = env.get("SYSTEMROOT", r"C:\Windows")
    env.update({
        "PATH": f"{root}\\System32;{root};{root}\\System32\\WindowsPowerShell\\v1.0",
        "TEMP": tmpdir,
        "TMP": tmpdir,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


# --- Windows Job Object: memory cap, and the whole process tree dies when the job closes ---

class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000


def _confine(proc: subprocess.Popen, memory_mb: int):
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_JOB_MEMORY | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        info.JobMemoryLimit = int(memory_mb) * 1024 * 1024
        kernel32.SetInformationJobObject(job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info))
        kernel32.AssignProcessToJobObject(job, int(proc._handle))
        return job
    except (OSError, AttributeError):
        return None


def _close_job(job) -> None:
    if job:
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle(job)


def run_sandbox(code: str, *, expected_name: str, must_match: str, inputs: list[str],
                timeout_s: float = 20, memory_mb: int = 256) -> SandboxResult:
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="jarvis_sandbox_", ignore_cleanup_errors=True) as tmp:
        candidate = Path(tmp) / "candidate_skill.py"
        candidate.write_text(code, encoding="utf-8")
        cases = Path(tmp) / "cases.json"
        cases.write_text(json.dumps({"expected_name": expected_name, "must_match": must_match,
                                     "inputs": inputs or [must_match]}), encoding="utf-8")
        # No -I here (that would hide user-site packages we pip-install for skills); the scrubbed env
        # below is the real isolation - it strips API keys - and static checks reject destructive code.
        cmd = [python_executable(), "-X", "utf8", str(HARNESS), str(candidate), str(cases),
               str(CONTRACT), str(ACTIONS)]
        proc = subprocess.Popen(cmd, cwd=tmp, env=_sandbox_env(tmp), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        job = _confine(proc, memory_mb)
        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return SandboxResult(False, f"timed out after {timeout_s:.0f}s: an infinite loop, a blocking call, "
                                        f"or a slow network request without a timeout", duration_s=time.monotonic() - start)
        finally:
            _close_job(job)

    duration = time.monotonic() - start
    out_text = stdout.decode("utf-8", "replace")
    for line in reversed(out_text.splitlines()):
        if line.startswith(MARKER):
            try:
                report = json.loads(line[len(MARKER):])
            except ValueError:
                break
            if report.get("ok"):
                return SandboxResult(True, outputs=report.get("outputs", []), duration_s=duration)
            where = f"{report.get('stage', 'sandbox')} stage"
            if report.get("input"):
                where += f", input {report['input']!r}"
            return SandboxResult(False, f"{where}:\n{report.get('error', 'unknown error')}",
                                 outputs=report.get("outputs", []), duration_s=duration)

    err_text = stderr.decode("utf-8", "replace").strip()
    hint = " (it probably exceeded the sandbox memory limit)" if "MemoryError" in err_text else ""
    return SandboxResult(False, f"sandbox process exited with code {proc.returncode}{hint}:\n{err_text[-2000:]}",
                         duration_s=duration)
