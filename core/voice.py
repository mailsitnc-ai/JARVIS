"""Talk to JARVIS: "Jarvis, ..." wake word, on-device speech recognition, spoken replies.

  Listener   - reads the microphone continuously, cuts it into utterances with a simple energy voice
               detector, and transcribes each one ON THIS COMPUTER (faster-whisper, base.en). Only an
               utterance that starts with the wake word ("Jarvis", "Hey Jarvis", ...) is acted on;
               everything else is discarded immediately - never stored, logged or sent anywhere. Audio
               is never written to disk.
  Speaker    - speaks replies with the system voice (macOS 'Daniel', British, by default), and mutes
               the listener while talking so JARVIS doesn't hear itself.

"Jarvis" on its own gets "Yes, sir?" and the next thing you say (within a few seconds) is the command.
Say "Jarvis, stop" to cut off speech / interrupt the current task.

Config (jarvis config --set key=value): voice.enabled (start listening with JARVIS), voice.tts_voice, voice.rate,
voice.model (tiny.en / base.en / small.en), voice.speak_replies ("voice" = only answer aloud when you
spoke, "always", "never"), voice.wake_words.
"""
from __future__ import annotations

import collections
import queue
import re
import subprocess
import sys
import threading
import time

RATE = 16000
BLOCK = 480                      # 30 ms frames

_WAKE = re.compile(
    r"^\W*(?:(?:hey|hi|hello|ok|okay|yo|oi|so|um|uh|and)\W+)*(?:jarvis|jarvi|jervis|jarvus|jarviss|travis)\b"
    r"[\s,.!?:;-]*", re.IGNORECASE)
_STOP = re.compile(r"^(?:stop|shut up|quiet|be quiet|cancel|never ?mind|that'?s all|enough)\W*$", re.IGNORECASE)
_THANKS = re.compile(r"^(?:thanks?|thank you|cheers|good job|nice|great)\b.*$", re.IGNORECASE)


# ---- pure helpers (unit-tested) ---------------------------------------------------------------

def parse_wake(text: str, extra_words=()) -> tuple[bool, str]:
    """(woke, command) - was this utterance addressed to JARVIS, and what came after the name?"""
    text = (text or "").strip()
    m = _WAKE.match(text)
    if not m and extra_words:
        alt = re.compile(r"^\W*(?:" + "|".join(re.escape(w) for w in extra_words) + r")\b[\s,.!?:;-]*", re.I)
        m = alt.match(text)
    if not m:
        return False, ""
    command = text[m.end():].strip().strip(".").strip()
    return True, command


def speakable(text: str, limit: int = 320) -> str:
    """Turn a panel reply into something worth saying aloud: no URLs, paths, markdown or code, and
    at most a couple of sentences (the full answer stays in the panel)."""
    t = str(text or "")
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"https?://\S+", "(link in the panel)", t)
    t = re.sub(r"(?:~|/Users/[^/\s]+)(/[^\s,]+)+", lambda m: m.group(0).rsplit("/", 1)[-1], t)
    t = re.sub(r"[*_`#>|]+", " ", t)
    t = re.sub(r"^\s*[-•]\s*", "", t, flags=re.M)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"(\(link in the panel\)\s*){2,}", "(links in the panel) ", t)
    if len(t) <= limit:
        return t
    cut = t[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[:end + 1] if end > 80 else cut.rsplit(" ", 1)[0] + "...") + " The rest is in the panel."


class Segmenter:
    """Energy-based voice-activity detection: feed 30 ms float32 frames, get whole utterances back.
    The noise floor adapts, so it works in a quiet room and near a fan."""

    def __init__(self, start_ratio=3.0, min_level=0.008, end_silence=0.8, max_len=12.0, min_len=0.35,
                 preroll=0.3):
        self.start_ratio, self.min_level = start_ratio, min_level
        self.end_frames = int(end_silence * RATE / BLOCK)
        self.max_frames = int(max_len * RATE / BLOCK)
        self.min_frames = int(min_len * RATE / BLOCK)
        self.pre = collections.deque(maxlen=int(preroll * RATE / BLOCK))
        self.floor = 0.003
        self.active, self.frames, self.silent = False, [], 0

    def level(self, frame) -> float:
        import numpy as np
        return float(np.sqrt(np.mean(np.square(frame)))) if len(frame) else 0.0

    def feed(self, frame):
        """Returns a finished utterance (np.float32 array) or None."""
        import numpy as np
        lvl = self.level(frame)
        loud = lvl > max(self.min_level, self.floor * self.start_ratio)
        if not self.active:
            self.floor = 0.97 * self.floor + 0.03 * min(lvl, 0.05)    # track background noise
            self.pre.append(frame)
            if loud:
                self.active, self.frames, self.silent = True, list(self.pre), 0
            return None
        self.frames.append(frame)
        self.silent = 0 if loud else self.silent + 1
        if self.silent >= self.end_frames or len(self.frames) >= self.max_frames:
            voiced = len(self.frames) - self.silent
            audio = np.concatenate(self.frames).astype(np.float32)
            self.active, self.frames, self.silent = False, [], 0
            self.pre.clear()
            return audio if voiced >= self.min_frames else None
        return None


# ---- speaking ---------------------------------------------------------------------------------

class Speaker:
    def __init__(self, voice: str = "Daniel", rate: int = 190):
        self.voice, self.rate = voice, int(rate)
        self.speaking = threading.Event()
        self._proc = None
        self._lock = threading.Lock()

    def _argv(self, text):
        from core import oslayer
        if oslayer.IS_MAC:
            argv = ["say", "-r", str(self.rate)]
            if self.voice:
                argv += ["-v", self.voice]
            return argv + [text]
        return oslayer.speak_command(text)

    def say(self, text: str, wait: bool = False) -> None:
        text = speakable(text)
        if not text:
            return

        def run():
            with self._lock:
                self.stop()
                self.speaking.set()
                try:
                    self._proc = subprocess.Popen(self._argv(text), stdout=subprocess.DEVNULL,
                                                  stderr=subprocess.DEVNULL)
                    self._proc.wait()
                except OSError:
                    pass
                finally:
                    self._proc = None
                    time.sleep(0.25)          # let the room echo die before listening again
                    self.speaking.clear()
        if wait:
            run()
        else:
            threading.Thread(target=run, daemon=True).start()

    def stop(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()


# ---- listening --------------------------------------------------------------------------------

class Listener:
    """Mic -> utterances -> local transcription -> wake word -> on_command(text)."""

    FOLLOW_UP = 7.0     # s after a bare "Jarvis" during which the next utterance is the command
    GATE_S = 2.5        # only the first seconds are checked for the wake word; the rest is transcribed
                        # only if they were addressed to JARVIS (a 14 s chat costs ~18 CPU-s to transcribe
                        # fully, ~2 CPU-s to gate - background talk/TV no longer pins the CPU)

    def __init__(self, on_command, on_state=None, speaker: Speaker | None = None, vocab=None,
                 model: str = "base.en", wake_words=()):
        self.on_command = on_command
        self.on_state = on_state or (lambda *_: None)
        self.speaker = speaker
        self.vocab = vocab or (lambda: [])
        self.model_name = model
        self.wake_words = tuple(wake_words or ())
        self.error = None
        self._stop = threading.Event()
        self._thread = None
        self._model = None
        self._q: queue.Queue = queue.Queue(maxsize=400)
        self._awake_until = 0.0

    def start(self):
        self._thread = threading.Thread(target=self._run, name="jarvis-voice", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def _load(self):
        from faster_whisper import WhisperModel
        try:   # already downloaded -> no network at all (works offline, nothing phones home)
            self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8", cpu_threads=2,
                                       local_files_only=True)
        except Exception:   # first run: fetch the model once (~140 MB for base.en)
            self.on_state("downloading")
            self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8", cpu_threads=2)

    def _prompt(self) -> str:
        names = [n for n in self.vocab() if n][:40]
        return "Jarvis. " + (("Names: " + ", ".join(names) + ". ") if names else "") + \
            "WhatsApp, Google Docs, Gmail, Chrome."

    def transcribe(self, audio, prompt: bool = True) -> str:
        segments, _ = self._model.transcribe(audio, beam_size=1, language="en", vad_filter=False,
                                             initial_prompt=self._prompt() if prompt else None,
                                             condition_on_previous_text=False, without_timestamps=True)
        return " ".join(s.text for s in segments).strip()

    def _gate(self, audio) -> bool:
        """Cheap check of the opening words for the wake word (no vocabulary prompt here, so noise isn't
        nudged into sounding like 'Jarvis')."""
        head = audio[:int(self.GATE_S * RATE)]
        return parse_wake(self.transcribe(head, prompt=False), self.wake_words)[0]

    def _callback(self, indata, frames, time_info, status):
        try:
            self._q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass

    def _run(self):
        try:
            import sounddevice as sd
            self.on_state("loading")
            self._load()
        except Exception as exc:
            self.error = f"voice isn't available: {exc}"
            self.on_state("error", self.error)
            return
        seg = Segmenter()
        try:
            stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=BLOCK,
                                    callback=self._callback)
            stream.start()
        except Exception as exc:
            self.error = (f"couldn't open the microphone ({exc}) - allow JARVIS in System Settings > "
                          "Privacy & Security > Microphone")
            self.on_state("error", self.error)
            return
        self.on_state("listening")
        try:
            while not self._stop.is_set():
                try:
                    frame = self._q.get(timeout=0.3)
                except queue.Empty:
                    continue
                if self.speaker and self.speaker.speaking.is_set():
                    seg = Segmenter() if seg.active else seg     # drop our own voice
                    continue
                audio = seg.feed(frame)
                if audio is None:
                    continue
                self._handle(audio)
        finally:
            stream.stop()
            stream.close()
            self.on_state("off")

    def _handle(self, audio):
        try:
            awake = time.time() < self._awake_until
            if not awake and len(audio) > (self.GATE_S + 0.5) * RATE and not self._gate(audio):
                return                                  # not for us: discarded, never logged
            text = self.transcribe(audio)
        except Exception as exc:
            self.on_state("error", f"transcription failed: {exc}")
            return
        finally:
            del audio                                   # never kept
        if not text:
            return
        now = time.time()
        woke, command = parse_wake(text, self.wake_words)
        if not woke and now < self._awake_until:        # the follow-up after a bare "Jarvis"
            woke, command = True, text.strip().strip(".")
        if not woke:
            return                                      # not for us: discarded, never logged
        self._awake_until = 0.0
        if not command:
            self._awake_until = now + self.FOLLOW_UP
            self.on_state("awake")
            if self.speaker:
                self.speaker.say("Yes, sir?")
            return
        if _THANKS.match(command) and len(command.split()) <= 4:
            if self.speaker:
                self.speaker.say("Always a pleasure, sir.")
            return
        if _STOP.match(command):
            if self.speaker:
                self.speaker.stop()
            self.on_command("__stop__")
            return
        self.on_command(command)


# ---- vocabulary: people you actually message ------------------------------------------------

def known_names(limit: int = 40) -> list[str]:
    """Contact/group names from past messaging requests, so Whisper spells them right."""
    try:
        import json
        from core.config import MEMORY_DIR
        from core.messaging import parse_message_command
        names: collections.Counter = collections.Counter()
        path = MEMORY_DIR / "interactions.jsonl"
        if not path.is_file():
            return []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("skill") not in ("whatsapp", "google_chat", "send_email"):
                continue
            to, _, _ = parse_message_command(str(row.get("request", "")))
            if to and not to.lstrip("+").isdigit():
                names[to.strip().title()] += 1
        return [n for n, _ in names.most_common(limit)]
    except Exception:
        return []


# ---- module-level control (the panel registers the handlers) ----------------------------------

_LISTENER: Listener | None = None
_SPEAKER: Speaker | None = None
_HANDLERS: dict = {}
_LOCK = threading.Lock()


def configure(on_command, on_state, settings) -> None:
    """Called once by the panel: how to run a voice command and show state, plus the voice settings."""
    global _SPEAKER
    _HANDLERS.update(on_command=on_command, on_state=on_state, settings=settings)
    _SPEAKER = Speaker(settings.get("voice.tts_voice", "Daniel") if sys.platform == "darwin" else "",
                       int(settings.get("voice.rate", 190) or 190))


def speaker() -> Speaker | None:
    return _SPEAKER


def start() -> str:
    global _LISTENER
    if not _HANDLERS:
        return "Voice needs the JARVIS panel running (start JARVIS, then say 'start listening')."
    with _LOCK:
        if _LISTENER is not None and _LISTENER.running():
            return "I'm already listening, sir. Just say \"Jarvis\" and your request."
        s = _HANDLERS["settings"]
        _LISTENER = Listener(_HANDLERS["on_command"], _HANDLERS["on_state"], _SPEAKER, known_names,
                             str(s.get("voice.model", "base.en") or "base.en"),
                             s.get("voice.wake_words", ()) or ())
        _LISTENER.start()
    return ("Listening. Say \"Jarvis\" followed by what you need - speech is processed on this Mac and "
            "anything not addressed to me is discarded.")


def stop() -> str:
    with _LOCK:
        if _LISTENER is not None and _LISTENER.running():
            _LISTENER.stop()
            return "I've stopped listening."
    return "I wasn't listening."


def listening() -> bool:
    return _LISTENER is not None and _LISTENER.running()
