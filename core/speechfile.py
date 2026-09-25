"""JARVIS's voice as a file, so a reply can be *heard* and not just read.

The panel speaks out loud through the speakers; a message on your phone needs the same words as an
audio file to attach. macOS does it with `say`, Windows with the speech engine built into Windows.

Files go in a private temp folder and are deleted as soon as they've been sent - nothing of what
JARVIS says to you is left lying around.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

MAC = sys.platform == "darwin"
WINDOWS = os.name == "nt"
LIMIT = 1200          # characters: past this it's a wall of speech nobody listens to
VOICE = "Daniel"      # the British voice JARVIS uses on macOS


def speakable(text: str) -> str:
    """Strip the things that sound awful read aloud: bullets, markdown, long paths, emoji."""
    body = re.sub(r"[\U0001f300-\U0001faff☀-➿]", "", str(text or ""))
    body = re.sub(r"`{1,3}[^`]*`{1,3}", " ", body)            # code spans
    body = re.sub(r"^\s*[-*•]\s*", "", body, flags=re.MULTILINE)
    body = re.sub(r"[*_#>]+", "", body)
    body = re.sub(r"\(/[^)]+\)|/Users/\S+", "", body)         # paths
    body = re.sub(r"\s*\n\s*", ". ", body)
    body = re.sub(r"\.{2,}", ".", body)
    return re.sub(r"\s{2,}", " ", body).strip()


def _folder() -> Path:
    folder = Path(tempfile.gettempdir()) / "jarvis-voice"
    folder.mkdir(mode=0o700, exist_ok=True)
    return folder


def to_audio(text: str, voice: str | None = None) -> Path | None:
    """Speak `text` into an audio file and return its path (None if this machine can't)."""
    words = speakable(text)
    if not words:
        return None
    if len(words) > LIMIT:
        words = words[:LIMIT].rsplit(".", 1)[0] + "."
    target = _folder() / f"jarvis-{uuid.uuid4().hex[:8]}"
    try:
        if MAC:
            path = target.with_suffix(".m4a")
            subprocess.run(["say", "-v", voice or VOICE, "-o", str(path), "--data-format=aac", words],
                           capture_output=True, timeout=90, check=False)
        elif WINDOWS:
            path = target.with_suffix(".wav")
            script = ("Add-Type -AssemblyName System.Speech; "
                      "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                      f"$s.SetOutputToWaveFile('{path}'); $s.Speak([Console]::In.ReadToEnd()); $s.Dispose()")
            subprocess.run(["powershell", "-NoProfile", "-Command", script], input=words, text=True,
                           capture_output=True, timeout=90, check=False)
        else:
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    return path if path.exists() and path.stat().st_size > 500 else None


def cleanup(path) -> None:
    """Delete one file we made (used as soon as it has been sent)."""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def sweep() -> None:
    """Delete anything an earlier session left behind."""
    try:
        for leftover in _folder().glob("jarvis-*"):
            cleanup(leftover)
    except OSError:
        pass
