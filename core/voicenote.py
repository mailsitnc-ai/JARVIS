"""Turn a WhatsApp voice note into words, on this machine.

You hold the mic button on your phone and talk; JARVIS has to hear it. WhatsApp Web keeps the audio
as a blob inside the page, so we ask the page itself to hand the bytes over (the blob belongs to
web.whatsapp.com, so only the page can fetch it), write them to a private temp file, and run the
same local Whisper the microphone uses. The audio never leaves the Mac, and the file is deleted as
soon as it has been read.
"""
from __future__ import annotations

import base64
import tempfile
import threading
import uuid
from pathlib import Path

# Play the newest voice note (WhatsApp only creates the <audio> element once you press play) and
# hand back the audio bytes. Returns "" until the blob exists, so the caller can retry.
PLAY_JS = ("(function(){var b=Array.prototype.slice.call(document.querySelectorAll("
           "'#main button[aria-label*=\"Play\" i], #main span[data-icon=\"audio-play\"]'));"
           "if(!b.length)return false;var t=b[b.length-1];(t.closest('button')||t).click();return true;})()")
PAUSE_JS = ("(function(){var a=document.querySelectorAll('#main audio');"
            "for(var i=0;i<a.length;i++){try{a[i].pause();a[i].currentTime=0;}catch(e){}}return true;})()")
GRAB_JS = ("(async function(){var a=document.querySelectorAll('#main audio');"
           "if(!a.length)return '';var el=a[a.length-1];if(!el.src)return '';"
           "try{var r=await fetch(el.src);var buf=new Uint8Array(await r.arrayBuffer());"
           "var s='';for(var i=0;i<buf.length;i++)s+=String.fromCharCode(buf[i]);return btoa(s);}"
           "catch(e){return '';}})()")

_MODEL = None
_LOCK = threading.Lock()


def _folder() -> Path:
    folder = Path(tempfile.gettempdir()) / "jarvis-voice"
    folder.mkdir(mode=0o700, exist_ok=True)
    return folder


def model(name: str = "base.en"):
    """The local Whisper model - the one the microphone is already using, if it's running."""
    global _MODEL
    from core import voice
    listener = getattr(voice, "_LISTENER", None)
    if listener is not None and getattr(listener, "_model", None) is not None:
        return listener._model
    with _LOCK:
        if _MODEL is None:
            from faster_whisper import WhisperModel
            try:
                _MODEL = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=2,
                                      local_files_only=True)
            except Exception:
                _MODEL = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=2)
    return _MODEL


def audio_bytes(controller, tries: int = 12, pause: float = 0.4) -> bytes:
    """Press play on the newest voice note and take the audio out of the page."""
    import time

    controller.evaluate(PLAY_JS)
    data = ""
    for _ in range(tries):
        time.sleep(pause)
        data = controller.evaluate(GRAB_JS, await_promise=True) or ""
        if data:
            break
    controller.evaluate(PAUSE_JS)
    try:
        return base64.b64decode(data) if data else b""
    except (ValueError, TypeError):
        return b""


def transcribe(controller, name: str = "base.en") -> str:
    """The words in the newest voice note in the open chat ('' if it couldn't be read)."""
    raw = audio_bytes(controller)
    if len(raw) < 1000:
        return ""
    path = _folder() / f"note-{uuid.uuid4().hex[:8]}.ogg"
    try:
        path.write_bytes(raw)
        segments, _ = model(name).transcribe(str(path), beam_size=1, language="en", vad_filter=False,
                                             condition_on_previous_text=False, without_timestamps=True)
        return " ".join(segment.text for segment in segments).strip()
    except Exception:
        return ""
    finally:
        try:
            path.unlink(missing_ok=True)      # the recording of your voice doesn't outlive the reply
        except OSError:
            pass
