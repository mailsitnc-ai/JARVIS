"""LLM router: one call site, several providers, automatic fallback.

JARVIS runs local-first: Ollama on this laptop, free, unlimited and offline. With
llm.provider = "auto" each call walks llm.fallback_order and uses the first provider that is
available and answers. The Groq provider is kept for anyone who adds it back to the order.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import keystore
from .config import Settings

USER_AGENT = "JARVIS/1.0 (Windows)"


class LLMError(RuntimeError):
    """A provider answered badly or not at all."""


class ProviderUnavailable(LLMError):
    """A provider cannot be used right now (not installed, offline, no key, rate limited out)."""


class _Rejected(LLMError):
    """The provider refused the request itself (HTTP 400)."""


@dataclass
class HttpResponse:
    status: int
    body: dict | None
    text: str
    headers: dict


def http_json(method: str, url: str, body: dict | None = None, headers: dict | None = None,
              timeout: float = 60) -> HttpResponse:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("User-Agent", USER_AGENT)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, raw, response_headers = response.status, response.read(), response.headers
    except urllib.error.HTTPError as exc:
        status, raw, response_headers = exc.code, exc.read(), exc.headers
    except (urllib.error.URLError, OSError) as exc:
        raise ProviderUnavailable(f"network error: {getattr(exc, 'reason', exc)}") from exc

    text = raw.decode("utf-8", "replace")
    try:
        parsed = json.loads(text) if text else None
    except ValueError:
        parsed = None
    lowered = {k.lower(): v for k, v in (response_headers.items() if response_headers else [])}
    return HttpResponse(status, parsed if isinstance(parsed, dict) else None, text, lowered)


def _error_detail(response: HttpResponse) -> str:
    error = (response.body or {}).get("error")
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])[:300]
    if isinstance(error, str):
        return error[:300]
    return response.text[:300]


def _retry_delay(headers: dict, attempt: int) -> float:
    try:
        return min(float(headers.get("retry-after", "")), 30.0)
    except ValueError:
        return min(2.0 ** attempt, 20.0)


class OpenAICompatProvider:
    """Any OpenAI-compatible chat API (Groq, Gemini's compat endpoint, MiniMax). Config lives under
    llm.<name>; the key is stored under <name>. Rate limits (429) and 5xx are retried, then the router
    falls through to the next provider in the chain."""

    name = "openai"
    token_param = "max_tokens"  # Groq wants max_completion_tokens; most others use max_tokens
    default_base_url = ""

    def __init__(self, settings: Settings, sleep=time.sleep):
        self.cfg = settings.get(f"llm.{self.name}", {}) or {}
        self.timeout = float(settings.get("llm.timeout_s", 60))
        self.max_retries = int(settings.get("llm.max_retries", 4))
        self._sleep = sleep
        self.last_usage: dict = {}

    @property
    def model(self) -> str:
        return str(self.cfg.get("model", ""))

    def available(self) -> tuple[bool, str]:
        source = keystore.key_source(self.name)
        if source:
            return True, source
        return False, f"no API key (run: jarvis setkey {self.name})"

    def chat(self, messages: list[dict], temperature: float, max_tokens: int, **_options) -> str:
        key = keystore.get_key(self.name)
        if not key:
            raise ProviderUnavailable(f"{self.name}: no API key")
        base = str(self.cfg.get("base_url", self.default_base_url)).rstrip("/")
        url = base + "/chat/completions"
        body = {"model": self.model, "messages": messages, "temperature": temperature,
                self.token_param: max_tokens}
        extra = dict(self.cfg.get("extra") or {})
        headers = {"Authorization": f"Bearer {key}"}

        retries = 0
        while True:
            response = http_json("POST", url, {**body, **extra}, headers, self.timeout)
            if response.status == 200:
                try:
                    content = response.body["choices"][0]["message"].get("content") or ""
                except (TypeError, KeyError, IndexError):
                    raise LLMError(f"{self.name}: unexpected response: {response.text[:200]}")
                if not content.strip():
                    raise LLMError(f"{self.name}: empty response")
                usage = (response.body or {}).get("usage") or {}
                self.last_usage = {"prompt": usage.get("prompt_tokens", 0),
                                   "completion": usage.get("completion_tokens", 0)}
                return content

            detail = _error_detail(response)
            if response.status == 400 and extra:
                extra = {}  # an optional parameter this model rejects; retry without it
                continue
            if response.status in (401, 403):
                raise ProviderUnavailable(f"{self.name}: HTTP {response.status}: {detail}")
            if response.status == 429 or response.status >= 500:
                if retries >= self.max_retries:
                    raise ProviderUnavailable(f"{self.name}: HTTP {response.status} after {retries} retries: {detail}")
                retries += 1
                self._sleep(_retry_delay(response.headers, retries))
                continue
            raise LLMError(f"{self.name}: HTTP {response.status}: {detail}")


class GroqProvider(OpenAICompatProvider):
    name = "groq"
    token_param = "max_completion_tokens"
    default_base_url = "https://api.groq.com/openai/v1"


class GeminiProvider(OpenAICompatProvider):
    name = "gemini"
    default_base_url = "https://generativelanguage.googleapis.com/v1beta/openai"




def ollama_environment() -> dict:
    """This process's environment plus OLLAMA_* user variables saved in the registry.

    A process started before those variables were saved (like a terminal opened earlier) has not
    inherited them, and the Ollama server would then run without its memory tuning.
    """
    env = dict(os.environ)
    try:
        import winreg  # Windows only; on macOS/Linux OLLAMA_* come from the process env already

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            index = 0
            while True:
                try:
                    name, value, _kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                if name.upper().startswith("OLLAMA_"):
                    env.setdefault(name, str(value))
                index += 1
    except (OSError, ImportError):
        pass
    return env


def ollama_executable() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    for default in (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
                    Path("/usr/local/bin/ollama"), Path("/opt/homebrew/bin/ollama"),
                    Path.home() / ".ollama" / "ollama"):
        if default.exists():
            return str(default)
    return None


class OllamaProvider:
    """Local models through Ollama. Streams replies so slow CPU generation shows progress."""

    name = "ollama"

    def __init__(self, settings: Settings):
        self.cfg = settings.get("llm.ollama", {}) or {}
        self._tried_autostart = False
        self.last_usage: dict = {}

    @property
    def model(self) -> str:
        return str(self.cfg.get("model", ""))

    @property
    def base_url(self) -> str:
        return str(self.cfg.get("base_url", "http://127.0.0.1:11434")).rstrip("/")

    def _get(self, path: str) -> dict | None:
        try:
            response = http_json("GET", self.base_url + path, timeout=2)
        except ProviderUnavailable:
            return None
        return (response.body or {}) if response.status == 200 else None

    def _autostart(self) -> bool:
        """Start `ollama serve` in the background, so Ollama need not run at sign-in and hold RAM."""
        executable = ollama_executable()
        local = "127.0.0.1" in self.base_url or "localhost" in self.base_url
        if self._tried_autostart or not executable or not local or not self.cfg.get("autostart", True):
            return False
        self._tried_autostart = True
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([executable, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True, env=ollama_environment())
        for _ in range(40):
            time.sleep(0.5)
            if self._get("/api/tags") is not None:
                return True
        return False

    def available(self) -> tuple[bool, str]:
        tags = self._get("/api/tags")
        if tags is None and self._autostart():
            tags = self._get("/api/tags")
        if tags is None:
            if ollama_executable():
                return False, f"installed but not running at {self.base_url}"
            return False, "Ollama is not installed (https://ollama.com/download)"
        names = {m.get("name") for m in tags.get("models", []) if isinstance(m, dict)}
        if self.model not in names and f"{self.model}:latest" not in names:
            return False, f"model not pulled (run: ollama pull {self.model})"
        return True, "running locally"

    def _warn_if_short_on_memory(self, progress) -> None:
        if progress is None:
            return
        loaded = self._get("/api/ps") or {}
        if any(m.get("name") == self.model for m in loaded.get("models", []) if isinstance(m, dict)):
            return  # already in memory, so low free RAM is just the model itself
        try:
            from .winsys import memory_status

            status = memory_status()
        except OSError:
            return
        free_mb = status.available_gb * 1024 if status else None
        if free_mb is not None and free_mb < int(self.cfg.get("min_free_mb", 1200)):
            progress(f"Only {free_mb:.0f} MB of RAM free; closing Chrome or other big apps will make this much faster.")

    def chat(self, messages: list[dict], temperature: float, max_tokens: int, *,
             json_schema: dict | None = None, progress=None) -> str:
        body = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "keep_alive": self.cfg.get("keep_alive", "3m"),
            "options": {"temperature": temperature, "num_predict": max_tokens,
                        "num_ctx": int(self.cfg.get("num_ctx", 4096))},
        }
        if self.cfg.get("think") is not None:
            body["think"] = bool(self.cfg["think"])
        if json_schema:
            body["format"] = json_schema
        self._warn_if_short_on_memory(progress)
        try:
            return self._stream(body, progress)
        except _Rejected as exc:
            if "think" in body and "think" in str(exc).lower():
                body.pop("think")  # this model has no thinking mode to switch off
                return self._stream(body, progress)
            raise LLMError(f"ollama: {exc}") from exc

    def _stream(self, body: dict, progress) -> str:
        request = urllib.request.Request(self.base_url + "/api/chat", data=json.dumps(body).encode("utf-8"),
                                         method="POST", headers={"Content-Type": "application/json",
                                                                 "User-Agent": USER_AGENT})
        if progress:
            progress(f"Local model {self.model} is reading the request...")
        try:
            response = urllib.request.urlopen(request, timeout=float(self.cfg.get("timeout_s", 1200)))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                detail = json.loads(detail).get("error", detail)
            except (ValueError, AttributeError):
                pass
            if exc.code == 400:
                raise _Rejected(str(detail)[:300]) from exc
            if exc.code == 404:
                raise ProviderUnavailable(f"ollama: {str(detail)[:200]}") from exc
            raise LLMError(f"ollama: HTTP {exc.code}: {str(detail)[:300]}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ProviderUnavailable(f"ollama: {getattr(exc, 'reason', exc)}") from exc

        parts: list[str] = []
        first_token_at = None
        last_report = time.monotonic()
        try:
            with response:
                for raw in response:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except ValueError:
                        continue
                    if chunk.get("error"):
                        raise LLMError(f"ollama: {chunk['error']}")
                    piece = (chunk.get("message") or {}).get("content") or ""
                    if piece:
                        parts.append(piece)
                        now = time.monotonic()
                        first_token_at = first_token_at or now
                        if progress and now - last_report >= 4:
                            rate = len(parts) / max(now - first_token_at, 1e-6)
                            progress(f"Local model writing: {len(parts)} tokens ({rate:.1f}/s)")
                            last_report = now
                    if chunk.get("done"):
                        self.last_usage = {"prompt": chunk.get("prompt_eval_count", 0),
                                           "completion": chunk.get("eval_count", 0)}
                        break
        except OSError as exc:
            raise LLMError(f"ollama: stream interrupted: {exc}") from exc

        text = "".join(parts)
        if not text.strip():
            raise LLMError("ollama: empty response")
        return text


PROVIDERS = {"groq": GroqProvider, "gemini": GeminiProvider, "ollama": OllamaProvider}


class LLMRouter:
    def __init__(self, settings: Settings, providers: dict | None = None, usage=None):
        self.settings = settings
        self.providers = providers if providers is not None else {name: cls(settings) for name, cls in PROVIDERS.items()}
        self.last_provider: str | None = None
        self.on_progress = None  # callable(str) for status updates during slow local generation
        if usage is None:
            from .usage import UsageStore
            usage = UsageStore()
        self.usage = usage

    def order(self) -> list[str]:
        pinned = str(self.settings.get("llm.provider", "auto") or "auto").strip().lower()
        if pinned != "auto":
            return [pinned]
        order = self.settings.get("llm.fallback_order") or list(self.providers)
        if isinstance(order, str):
            order = order.split(",")
        seen: list[str] = []
        for name in order:
            name = str(name).strip().lower()
            if name and name not in seen:
                seen.append(name)
        return seen

    def complete(self, prompt: str | None = None, *, messages: list[dict] | None = None, system: str | None = None,
                 temperature: float = 0.2, max_tokens: int = 1024, json_schema: dict | None = None) -> str:
        if messages is None:
            messages = ([{"role": "system", "content": system}] if system else []) + \
                       [{"role": "user", "content": prompt or ""}]
        failures: list[str] = []
        for name in self.order():
            provider = self.providers.get(name)
            if provider is None:
                failures.append(f"{name}: unknown provider")
                continue
            ok, reason = provider.available()
            if not ok:
                failures.append(f"{name}: {reason}")
                continue
            try:
                text = provider.chat(messages, temperature, max_tokens, json_schema=json_schema,
                                     progress=self.on_progress)
            except LLMError as exc:
                failures.append(str(exc))
                continue
            self.last_provider = name
            u = getattr(provider, "last_usage", None) or {}
            try:
                self.usage.record(name, u.get("prompt", 0), u.get("completion", 0))
            except Exception:
                pass
            return text
        raise LLMError("no LLM provider could answer: " + (" | ".join(failures) or "none configured"))

    def status(self) -> list[tuple[str, bool, str, str]]:
        """Availability of each provider in the active order."""
        rows = []
        for name in self.order():
            provider = self.providers.get(name)
            if provider is None:
                rows.append((name, False, "unknown provider", ""))
                continue
            ok, reason = provider.available()
            rows.append((name, ok, reason, getattr(provider, "model", "")))
        return rows
