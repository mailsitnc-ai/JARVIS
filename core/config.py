"""Layered settings for JARVIS.

Precedence, lowest to highest:
  1. DEFAULTS below
  2. %APPDATA%\\JARVIS\\config.json      (written by `jarvis config --set key=value`)
  3. JARVIS_* environment variables; "__" separates nesting and values are parsed as JSON:
       $env:JARVIS_LLM__FALLBACK_ORDER = '["ollama","groq"]'

API keys are not settings; they live in core/keystore.py. Storing a key must never change
which provider is used: the old build pinned llm.provider on setkey, which silently
overrode the fallback order.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"
MEMORY_DIR = ROOT / "memory" / "data"
ARCHIVE_DIR = ROOT / "evolution_engine" / "archive"

DEFAULTS: dict[str, Any] = {
    "llm": {
        # "auto" walks fallback_order; a provider name pins every call to that provider.
        "provider": "auto",
        # Try the free cloud brains first (their limits stack), then local offline.
        # Cloud only runs once you add a key; until then it falls straight through to Ollama.
        "fallback_order": ["groq", "gemini", "ollama"],
        "timeout_s": 60,
        "max_retries": 4,
        "groq": {
            "model": "openai/gpt-oss-120b",
            "base_url": "https://api.groq.com/openai/v1",
            "extra": {"reasoning_effort": "low"},
        },
        "gemini": {
            # gemini-2.5-flash was retired (404). "gemini-flash-latest" always points at the current flash
            # (multimodal, for vision). NOTE: needs a Gemini key whose project isn't denied API access -
            # get one at https://aistudio.google.com/apikey if calls return 403 PERMISSION_DENIED.
            "model": "gemini-flash-latest",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "extra": {},
        },
        "ollama": {
            # Full-precision 0.5B coder: half the RAM of the 1.5B. Swap in "qwen2.5-coder:1.5b" for more
            # capable skill drafts if you can close other apps while it works.
            "model": "qwen2.5-coder:0.5b-instruct-q8_0",
            "base_url": "http://127.0.0.1:11434",
            "num_ctx": 4096,
            "keep_alive": "3m",  # unload after 3 idle minutes so the RAM comes back
            "think": False,  # thinking models would spend minutes reasoning on this CPU
            "autostart": True,  # start `ollama serve` when JARVIS needs it
            "min_free_mb": 1200,  # warn when other apps leave the model less than this
            "timeout_s": 1200,
        },
    },
    "evolution": {
        "enabled": True,
        "max_attempts": 3,
        "sandbox_timeout_s": 20,
        "sandbox_memory_mb": 256,
        # Evolved skills may not delete files, eval/exec, use ctypes or shell=True unless this is on.
        "allow_risky_calls": False,
    },
    "window": {
        "hotkey": "ctrl+alt+j",
        "interrupt_hotkey": "ctrl+alt+c",  # stop whatever JARVIS is currently doing
        # Which browser to open links/searches/tabs in: "chrome" reuses your Chrome window;
        # "default" uses the OS default; also "edge"/"firefox"/"brave" or a full path to the .exe.
        "browser": "chrome",
        # Chrome DevTools remote-debugging port for DOM control (read/click/type). JARVIS launches its
        # own Chrome window on this port with a dedicated profile, so it never disturbs your normal Chrome.
        "debug_port": 9222,
        "split": 0.6,  # share of the screen kept by the window you were working in
        "jarvis_side": "right",
        "ipc_port": 47821,
    },
    "memory": {
        "max_interactions": 5000,
        "recall_k": 3,
    },
    "agent": {
        # Multi-step tasks run an act-observe-adapt loop: do a step, read the real result,
        # decide the next. This caps how many steps it may take before it must finish.
        "max_steps": 5,
    },
    "self_edit": {
        # When JARVIS rewrites its own source, run the full test suite and roll back if it fails.
        # Turning this off removes the safety net - a broken self-edit could then stick.
        "run_tests": True,
        # Commit each successful self-edit to git (starting a repo if there isn't one), so every
        # change is a revertible commit. Needs git on PATH; skipped silently if it's missing.
        "git_commit": True,
    },
}

_MISSING = object()


def user_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    path = Path(base) / "JARVIS"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_config_path() -> Path:
    return user_dir() / "config.json"


def parse_value(raw: str) -> Any:
    """JSON when possible. Also accepts [a,b] because PowerShell 5 strips the inner quotes."""
    text = raw.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    if text.startswith("[") and text.endswith("]"):
        return [item.strip().strip("'\"") for item in text[1:-1].split(",") if item.strip()]
    return raw


def write_json_atomic(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _deep_merge(base: dict, patch: dict) -> dict:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def _lookup(tree: dict, dotted: str) -> Any:
    node: Any = tree
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _env_overrides(environ) -> dict:
    out: dict = {}
    for name, raw in environ.items():
        if not name.upper().startswith("JARVIS_"):
            continue
        parts = [p.lower() for p in name[len("JARVIS_"):].split("__") if p]
        if not parts or parts[0] not in DEFAULTS:
            continue
        node = out
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = parse_value(raw)
    return out


def read_user_config() -> dict:
    try:
        data = json.loads(user_config_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class Settings:
    def __init__(self, data: dict, user: dict, env: dict):
        self.data = data
        self._user = user
        self._env = env

    def get(self, dotted: str, default: Any = None) -> Any:
        value = _lookup(self.data, dotted)
        return default if value is _MISSING else value

    def origin(self, dotted: str) -> str:
        if _lookup(self._env, dotted) is not _MISSING:
            return "environment"
        if _lookup(self._user, dotted) is not _MISSING:
            return "user config"
        return "default"


def load_settings(environ=None) -> Settings:
    user = read_user_config()
    env = _env_overrides(os.environ if environ is None else environ)
    data = _deep_merge(_deep_merge(copy.deepcopy(DEFAULTS), user), env)
    return Settings(data, user, env)


def set_user_value(dotted: str, value: Any) -> None:
    config = read_user_config()
    node = config
    parts = dotted.split(".")
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = node[part] = {}
        node = child
    node[parts[-1]] = value
    write_json_atomic(user_config_path(), config)


def unset_user_value(dotted: str) -> bool:
    config = read_user_config()
    node = config
    parts = dotted.split(".")
    for part in parts[:-1]:
        node = node.get(part)
        if not isinstance(node, dict):
            return False
    if parts[-1] not in node:
        return False
    del node[parts[-1]]
    write_json_atomic(user_config_path(), config)
    return True
