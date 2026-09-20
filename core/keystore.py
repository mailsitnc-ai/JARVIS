"""API key storage. On Windows keys are encrypted with DPAPI (only this Windows account can read them);
on macOS/Linux they are base64-obfuscated in a file locked to the user (chmod 600) - not true encryption,
so on those systems prefer the environment variables below for anything sensitive.

An environment variable (GROQ_API_KEY) wins over the stored key. Storing a key writes the key and
nothing else: it never touches llm.provider, so the fallback order keeps working.
"""
from __future__ import annotations

import base64
import json
import os

from .config import user_dir, write_json_atomic
from .oslayer import IS_WINDOWS

ENV_VARS = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY"}
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DataBlob()
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(blob_in), "JARVIS API key", None, None, None,
                                      _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None,
                                        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def _encode(key: str) -> str:
    """Serialise a key for storage. DPAPI-encrypted on Windows, base64-obfuscated elsewhere."""
    raw = key.encode("utf-8")
    blob = _dpapi(raw, protect=True) if IS_WINDOWS else raw
    return base64.b64encode(blob).decode("ascii")


def _decode(encoded: str) -> str:
    blob = base64.b64decode(encoded)
    raw = _dpapi(blob, protect=False) if IS_WINDOWS else blob
    return raw.decode("utf-8")


def _path():
    path = user_dir() / "keys.json"
    return path


def _read() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def store_key(provider: str, key: str) -> None:
    data = _read()
    data[provider] = _encode(key)
    path = _path()
    write_json_atomic(path, data)
    if not IS_WINDOWS:
        try:
            os.chmod(path, 0o600)  # user-only, since it isn't DPAPI-encrypted off Windows
        except OSError:
            pass


def clear_key(provider: str) -> bool:
    data = _read()
    if provider not in data:
        return False
    del data[provider]
    write_json_atomic(_path(), data)
    return True


def stored_key(provider: str) -> str | None:
    encoded = _read().get(provider)
    if not encoded:
        return None
    try:
        return _decode(encoded)
    except (OSError, ValueError):
        return None


def get_key(provider: str) -> str | None:
    env_name = ENV_VARS.get(provider)
    from_env = os.environ.get(env_name, "").strip() if env_name else ""
    return from_env or stored_key(provider)


def key_source(provider: str) -> str | None:
    env_name = ENV_VARS.get(provider)
    if env_name and os.environ.get(env_name, "").strip():
        return f"${env_name}"
    if stored_key(provider):
        return "stored key (DPAPI)" if IS_WINDOWS else "stored key"
    return None
