"""JARVIS on your phone: it watches one WhatsApp chat and does what you ask there.

Message your own number ("Message yourself") from the phone - "Jarvis, what's my battery?", "jarvis
summarise the physics pdf", "/briefing" - and JARVIS, running on the Mac, does it and answers in the
same chat. That's the Telegram bot from the video, except it goes through the WhatsApp Web session
JARVIS already drives, so it's your own number and there's nothing to sign up for.

Rules that keep it safe:
  - only the one chat you name is watched, and outside a conversation only messages that start with
    "jarvis" (or "/") count as commands - notes to yourself stay notes to yourself;
  - say just "Jarvis" (or address him by name once) and the conversation stays open: everything you
    send goes to him until you say "ok dismissed";
  - every reply is prefixed with a marker, so JARVIS never reads its own messages as commands;
  - anything risky (deleting, sending to other people, shell commands, settings) asks first, right
    there in the chat: it replies with the question and waits for your "yes".

The Mac has to be awake with JARVIS's Chrome open and WhatsApp Web linked - this is JARVIS reaching
through the browser, not a cloud bot.
"""
from __future__ import annotations

import collections
import datetime as dt
import queue
import re
import threading
import time

MARK = "\U0001f916"       # the robot face every reply starts with, so we never answer ourselves
# "jarvis ...", "hey jarvis ...", "/..." - and "JarvisCould you..." run together, which is what you
# end up typing on a phone when the wake word is compulsory.
WAKE = re.compile(r"^\s*(?:hey\s+|hi\s+|ok\s+|okay\s+|yo\s+)?jarvis(?:\b|(?=[A-Z]))[\s,:.\-]*"
                  r"|^\s*[/!]\s*", re.IGNORECASE)
# Just his name, nothing else: you're calling him, not asking for anything yet.
SUMMON = re.compile(r"^\s*(?:hey|hi|hello|yo|ok|okay|oye)?\s*jarvis\s*[!?.,]*\s*$", re.IGNORECASE)
# The only way out of a conversation, as the user put it: "ok dismissed".
DISMISS = re.compile(r"^\s*(?:ok(?:ay)?\s+|thanks?\s+|thank\s+you\s+|alright\s+|right\s+)*"
                     r"(?:jarvis\s+)?(?:you'?re\s+)?(?:dismissed?|stand\s*down|that'?(?:s|ll)\s+(?:be\s+)?all"
                     r"|we'?re\s+done|done\s+for\s+now|bye|goodbye|good\s*night)"
                     r"(?:\s+jarvis)?\s*[!.]*\s*$", re.IGNORECASE)
YES = re.compile(r"^\s*(?:y|yes+|yeah|yep|yup|ok|okay|sure|go|go\s+ahead|do\s+it|confirm|allow|"
                 r"please\s+do)\b", re.IGNORECASE)
NO = re.compile(r"^\s*(?:n|no+|nope|nah|stop|cancel|don'?t|deny|never\s*mind|nvm)\b", re.IGNORECASE)
ALWAYS = re.compile(r"\b(?:always|from\s+now\s+on|every\s+time)\b", re.IGNORECASE)

# A message from the phone is easy to send by accident and impossible to take back, and JARVIS may be
# running with autonomy on (which skips its usual questions), so the channel does its own asking for
# anything that can't be undone or that reaches other people.
RISKY = [
    (re.compile(r"\b(?:delete|remove|erase|wipe|trash|uninstall|format|empty\s+the\s+(?:bin|trash))\b",
                re.IGNORECASE), "delete something"),
    (re.compile(r"\b(?:send|message|text|whats\s?app|email|mail|dm|reply)\b[^\n]{0,40}?\b(?:to|for)\s+"
                r"(?!me\b|myself\b)[a-z@+\d]", re.IGNORECASE), "message someone else"),
    (re.compile(r"\b(?:post|publish|tweet|upload|share)\b", re.IGNORECASE), "put something out publicly"),
    (re.compile(r"\b(?:buy|order|pay|purchase|checkout|transfer)\b", re.IGNORECASE), "spend money"),
    (re.compile(r"\b(?:shut\s?down|restart|reboot|log\s?out|lock\s+the|sleep\s+the)\b", re.IGNORECASE),
     "shut the machine down or lock it"),
    (re.compile(r"\b(?:run|execute)\b[^\n]{0,20}\b(?:command|terminal|shell|script)\b|\bsudo\b|"
                r"\bpip\s+install\b|\bbrew\s+install\b", re.IGNORECASE), "run a command on the Mac"),
    (re.compile(r"\b(?:change|set|turn\s+off)\b[^\n]{0,20}\b(?:setting|settings|config|autonomy|"
                r"password|key)\b", re.IGNORECASE), "change how JARVIS is set up"),
]


def risk(command: str) -> str | None:
    """What's risky about this command, in plain words - None when there's nothing to worry about."""
    text = str(command or "")
    for pattern, why in RISKY:
        if pattern.search(text):
            return why
    return None

# WhatsApp stamps every bubble with "[1:57 pm, 25/9/2026] Name:" - the only way to tell a message
# that arrived just now from one that was already sitting in the chat.
STAMP = re.compile(r"\[(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([ap])\.?m\.?,\s*(\d{1,2})/(\d{1,2})/(\d{4})\]",
                   re.IGNORECASE)

_LOCAL = threading.local()


def message_time(meta: str):
    """When WhatsApp says this message was sent, or None if the stamp isn't one we know."""
    match = STAMP.search(str(meta or ""))
    if not match:
        return None
    hour, minute, second, half, day, month, year = match.groups()
    hour, minute = int(hour), int(minute)
    if half:
        hour = hour % 12 + (12 if half.lower() == "p" else 0)
    try:
        return dt.datetime(int(year), int(month), int(day), hour, minute, int(second or 0))
    except ValueError:
        return None


def same_words(a: str, b: str) -> bool:
    """Two messages that read the same once WhatsApp has had its way with emoji and spacing."""
    clean = lambda t: re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", str(t or "").lower())).strip()[:160]
    return bool(clean(a)) and clean(a) == clean(b)


def command_in(text: str, require_wake: bool = True) -> str | None:
    """The command inside a message, or None if the message isn't for JARVIS."""
    body = str(text or "").strip()
    if not body or body.startswith(MARK):
        return None                       # one of our own replies
    match = WAKE.match(body)
    if match:
        return body[match.end():].strip() or None
    return None if require_wake else body


def decision(text: str) -> str | None:
    """'once' / 'always' / 'deny' from a reply to a confirmation question, or None if it's neither."""
    body = str(text or "").strip()
    if NO.match(body):
        return "deny"
    if YES.match(body):
        return "always" if ALWAYS.search(body) else "once"
    return None


def describe(req) -> str:
    """The confirmation question, in the words the chat needs: what it wants to do, and how to answer."""
    what = getattr(req, "summary", None) or getattr(req, "description", None) or str(req)
    need = getattr(req, "capability", None) or getattr(req, "kind", None)
    line = f"{MARK} May I {what}?" if not str(what).startswith("May I") else f"{MARK} {what}"
    if need:
        line += f"\n(needs: {need})"
    return line + "\nReply YES to go ahead, NO to skip."


def current():
    """The phone channel handling THIS thread's request, if any - the panel asks it to confirm."""
    return getattr(_LOCAL, "channel", None)


class PhoneChannel:
    """Watches one WhatsApp chat and runs what you send it. `ask(text) -> reply` does the work."""

    POLL = 1.2                # seconds between looks at the chat
    HISTORY = 8               # how many of the last messages we re-read each time
    CONFIRM_WAIT = 90.0       # how long to wait for a "yes" before giving up
    SNAPSHOT_WAIT = 15.0      # how long to let WhatsApp draw the chat before reading it
    MAX_REPLY = 3500          # WhatsApp's message limit is bigger, but nobody reads more than this

    def __init__(self, ask, chat: str, emit=None, controller=None, require_wake: bool = True,
                 voice: bool = True, poll: float | None = None):
        self.ask = ask
        self.chat = str(chat)
        self.emit = emit or (lambda _text: None)
        self.controller = controller
        self.require_wake = bool(require_wake)
        self.voice = bool(voice)
        self.poll = float(poll or self.POLL)
        self.seen = set()
        self.started = None        # messages older than this were already in the chat when we arrived
        self.ours = collections.deque(maxlen=40)   # what we've said, so we never answer ourselves
        self.expecting = False     # our last reply asked YOU something, so the next message is the answer
        self.working = threading.Event()   # a request is using the browser: the watcher keeps off
        self.voice_fails = 0
        self.outbox = []           # replies that couldn't be delivered yet (Chrome was away)
        self.last_revive = 0.0     # when we last tried to bring Chrome back
        self.away = False          # Chrome/WhatsApp is currently unreachable
        self.blank_rounds = 0      # polls in a row with no chat on screen
        self.session = False       # you've summoned him: everything you send is for him, until
                                   # you say "ok dismissed"
        self.preapproved = False   # you already said yes to this one request; don't ask twice
        self.inbox = queue.Queue()
        self.error = None
        self.last_reply = ""
        self._stop = threading.Event()
        self._threads = []

    # ---- lifecycle ------------------------------------------------------------------------------
    def start(self) -> str:
        if self.running():
            return f"I'm already watching WhatsApp ({self.chat})."
        if self.controller is None:
            from core.browser import ChromeController
            from core.config import load_settings
            port = int(load_settings().get("window.debug_port", 9222) or 9222)
            self.controller = ChromeController(port=port)   # the same Chrome the browser skills drive
        opened = self.controller.whatsapp_open(self.chat)
        if not opened or opened.lower().startswith(("i couldn't", "whatsapp")):
            return opened or f"I couldn't open the WhatsApp chat '{self.chat}'."
        self.started = dt.datetime.now()
        self.seen = self._snapshot()          # the history is not a backlog of orders
        self._stop.clear()
        self._threads = [threading.Thread(target=self._watch, daemon=True),
                         threading.Thread(target=self._work, daemon=True)]
        for thread in self._threads:
            thread.start()
        return (f"Watching WhatsApp ({opened}). Message yourself \"Jarvis\" from your phone and "
                f"everything after that comes to me until you say \"ok dismissed\".")

    def stop(self) -> str:
        if not self.running():
            return "I wasn't watching WhatsApp."
        self._stop.set()
        return "Stopped watching WhatsApp."

    def running(self) -> bool:
        return any(t.is_alive() for t in self._threads) and not self._stop.is_set()

    # ---- the chat ------------------------------------------------------------------------------
    def _snapshot(self, wait: float | None = None) -> set:
        """Everything already in the chat, marked seen. WhatsApp can take a few seconds to draw the
        conversation after it opens, and reading too early once made JARVIS answer a message from
        an hour before."""
        deadline = time.monotonic() + (self.SNAPSHOT_WAIT if wait is None else wait)
        while time.monotonic() < deadline and not self._stop.is_set():
            rows = self._read()
            if rows:
                return {row.get("id") for row in rows}
            time.sleep(0.5)
        return set()

    def _read(self):
        try:
            return self.controller.whatsapp_messages(self.HISTORY)
        except Exception as exc:                       # a closed tab, a reload, a flaky socket
            self.error = str(exc)
            return []

    REVIVE_EVERY = 20.0        # seconds between attempts to bring Chrome back

    def _ensure_chat(self) -> bool:
        """Keep our chat open and reachable. You shouldn't have to keep Chrome running for this:
        if it has been quit, crashed, or the tab was closed, JARVIS starts it again itself."""
        try:
            if not self.controller.alive():         # Chrome has been quit or has crashed
                return self._revive()
            title = self.controller.whatsapp_open_chat_title()
            if title and (title == self.chat or self._same_chat(title)):
                self.blank_rounds = 0
                return self._back()
            if not title:
                self.blank_rounds += 1
                if self.blank_rounds < 3:
                    return False       # give the page a moment to draw before reloading it
            self.blank_rounds = 0
            opened = self.controller.whatsapp_open(self.chat)
            ok = bool(opened) and not opened.lower().startswith(("i couldn't", "whatsapp web isn't"))
            return self._back() if ok else self._revive()
        except Exception as exc:
            self.error = str(exc)
            return self._revive()

    def _revive(self) -> bool:
        """Chrome isn't answering: start it (JARVIS's own, with the linked WhatsApp) and re-open
        the chat. Tried on a timer so a machine with no Chrome doesn't spin."""
        if not self.away:
            self.away = True
            self.emit("WhatsApp is out of reach (Chrome isn't running) - bringing it back.")
        if time.monotonic() - self.last_revive < self.REVIVE_EVERY:
            return False
        self.last_revive = time.monotonic()
        try:
            self.controller.close()
            self.controller.ensure()          # launches JARVIS's own Chrome if there isn't one
            opened = self.controller.whatsapp_open(self.chat)
            if opened and not opened.lower().startswith(("i couldn't", "whatsapp web isn't")):
                return self._back()
            self.error = opened
        except Exception as exc:
            self.error = str(exc)
        return False

    def _back(self) -> bool:
        """We can see the chat again - say so once, and deliver anything that was waiting."""
        if self.away:
            self.away = False
            self.emit("WhatsApp is back - I'm watching your chat again.")
        while self.outbox:
            waiting = self.outbox.pop(0)
            self.say(waiting)
        return True

    def _on_our_chat(self) -> bool:
        """Only ever take orders from YOUR chat - never from whatever conversation happens to be
        open, or someone else's message could run a command on your Mac."""
        try:
            title = self.controller.whatsapp_open_chat_title()
        except Exception:
            return False
        return bool(title) and (title == self.chat or self._same_chat(title))

    def _same_chat(self, title: str) -> bool:
        digits = re.sub(r"\D", "", self.chat)
        return bool(digits) and digits[-8:] in re.sub(r"\D", "", title) or \
            str(title).strip().lower() in ("message yourself", "you", self.chat.strip().lower())

    def say(self, text: str, voice: bool | None = None) -> None:
        """Answer in the chat (and, when voice is on, as a voice note too)."""
        body = str(text or "").strip()
        if not body:
            return
        self.ours.append(body)     # WhatsApp turns our marker emoji into an image, so the text alone
                                   # can't prove a message is ours - remember what we said instead
        # "Which doc, sir?" - you shouldn't have to write "jarvis" again to answer a question.
        self.expecting = body.rstrip().endswith("?")
        if len(body) > self.MAX_REPLY:
            body = body[:self.MAX_REPLY] + "\n... (trimmed)"
        marked = body if body.startswith(MARK) else f"{MARK} {body}"
        self.last_reply = marked
        try:
            problem = self.controller.whatsapp_type_send(marked)
        except Exception as exc:
            problem = str(exc)
        if problem:
            # Don't lose the answer because Chrome went away mid-reply: hold it and send it when
            # the chat comes back.
            self.emit(f"WhatsApp reply failed ({problem}) - holding it until the chat is back.")
            if len(self.outbox) < 10 and body not in self.outbox:
                self.outbox.append(body)
            self.away = True
            return
        # A voice note takes a few seconds; if you've already sent the next thing, answer that
        # first and skip the audio - being quick matters more than being heard.
        if self.voice and (voice is not False) and self.inbox.empty():
            self._voice_note(body)
            self._forget_our_media()

    def _forget_our_media(self) -> None:
        """The voice note we just sent arrives as a new message with no text - and an untexted
        message is how an incoming voice note looks too. Mark ours as seen, or JARVIS tries to
        transcribe its own voice and treats it as your answer to its own question."""
        try:
            for row in self._read()[-3:]:
                if not str(row.get("text") or "").strip():
                    self.seen.add(row.get("id"))
        except Exception:
            pass

    def _voice_note(self, text: str) -> None:
        """JARVIS's own voice, as an audio message - like the bot in the video."""
        problem = None
        try:
            from core import speechfile
            path = speechfile.to_audio(text)
            if not path:
                return
            problem = self.controller.whatsapp_attach(str(path))
            speechfile.cleanup(path)
        except Exception as exc:
            problem = str(exc)
        finally:
            # However it went, leave WhatsApp in a state you can type in again: a half-finished
            # attachment used to block every later reply with "couldn't find the message box".
            try:
                self.controller.whatsapp_reset(self.chat)
            except Exception:
                pass
        if not problem:
            self.voice_fails = 0
            return
        self.voice_fails += 1
        self.emit(f"Voice note failed: {problem}")
        if self.voice_fails >= 2:      # it's costing more than it's worth - text is instant
            self.voice = False
            self.emit("Voice notes are off for now (they kept failing); replies are text only. "
                      "Say 'watch my whatsapp' again to retry them.")

    # ---- the loops -----------------------------------------------------------------------------
    def _watch(self) -> None:
        """Poll the chat and hand anything new to the worker."""
        while not self._stop.is_set():
            if self.working.is_set():
                self._stop.wait(self.poll)     # a request is driving the browser - don't fight it
                continue
            if self._ensure_chat() and self._on_our_chat():
                for message in self._read():
                    ident = message.get("id") or ""
                    if ident in self.seen:
                        continue
                    self.seen.add(ident)
                    if len(self.seen) > 400:
                        self.seen = set(list(self.seen)[-200:])
                    self.inbox.put(message)
            self._stop.wait(self.poll)

    def _work(self) -> None:
        """Run one request at a time, so a confirmation can wait for your next message."""
        _LOCAL.channel = self
        while not self._stop.is_set():
            try:
                message = self.inbox.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._handle(message)
            except Exception as exc:
                self.emit(f"Phone request failed: {exc}")
                self.say(f"That went wrong on my end, sir: {exc}")

    def _is_ours(self, text: str) -> bool:
        return str(text or "").lstrip().startswith(MARK) or any(same_words(text, mine) for mine in self.ours)

    def _too_old(self, message) -> bool:
        """Was this sitting in the chat before JARVIS started watching?"""
        when = message_time(message.get("meta"))
        return bool(when and self.started and when < self.started - dt.timedelta(minutes=2))

    def _handle(self, message) -> None:
        text = str(message.get("text") or "")
        if self._is_ours(text) or self._too_old(message):
            return
        if message.get("audio") and not text.strip():
            text = self._transcribe(message)
            if not text:
                return
        body = text.strip()
        if self.session and DISMISS.match(body):
            self.session = False
            self.say('Standing by, sir. Say "Jarvis" when you need me.', voice=False)
            return
        if SUMMON.match(body):              # just his name: he answers and stays listening
            self.session = True
            self.say('At your service, sir. Everything you send now comes to me - say '
                     '"ok dismissed" when you\'re done.', voice=False)
            return
        answering = self.expecting          # a reply to JARVIS's own question needs no wake word
        command = command_in(body, self.require_wake and not answering and not self.session)
        self.expecting = False
        if not command:
            return
        if WAKE.match(body):                # addressing him by name opens the conversation too
            self.session = True
        self.emit(f"WhatsApp: {command}")
        why = risk(command)
        if why and self.ask_yes_no(f'{MARK} That would {why}: "{command}".\n'
                                   f"Reply YES to go ahead, or NO to skip.") == "deny":
            self.say("Left it alone, sir.")
            return
        self.preapproved = bool(why)   # you've said yes once; the skill itself needn't ask again
        self.working.set()             # hands off the browser: this request may need the tab itself
        try:
            reply = str(self.ask(command) or "").strip()
        finally:
            self.preapproved = False
            try:                       # a skill may have gone off to someone else's chat
                self.controller.whatsapp_reset(self.chat)
            except Exception:
                pass
            self.working.clear()
        self.say(reply or "Done, sir.")

    def _transcribe(self, message) -> str:
        """A voice note from the phone, turned into words on this machine (nothing is uploaded)."""
        try:
            from core import voicenote
            heard = voicenote.transcribe(self.controller)
        except Exception as exc:
            self.emit(f"Voice note not read: {exc}")
            heard = ""
        if not heard:
            self.say("I couldn't hear that voice note, sir - send it as text and I'll get on with it.")
            return ""
        self.emit(f"WhatsApp voice note: {heard}")
        self.say(f'Heard: "{heard}"')
        return heard

    # ---- confirmations, in the chat --------------------------------------------------------------
    def confirm(self, req) -> str:
        """The broker asking permission mid-request: put it to the chat. 'once' / 'always' / 'deny'."""
        if self.preapproved:
            return "once"             # you already approved this request when it came in
        return self.ask_yes_no(describe(req))

    def ask_yes_no(self, question: str) -> str:
        """Ask in the chat and wait for the answer. Returns 'once' / 'always' / 'deny'."""
        self.say(question, voice=False)     # a spoken copy of the question only gets in the way
        # We may be in the middle of a request that has the browser to itself - but the answer can
        # only reach us through the chat, so the watcher has to be let back in while we wait.
        held = self.working.is_set()
        self.working.clear()
        try:
            return self._wait_for_answer()
        finally:
            if held:
                self.working.set()

    def _wait_for_answer(self) -> str:
        deadline = time.monotonic() + self.CONFIRM_WAIT
        while time.monotonic() < deadline and not self._stop.is_set():
            try:
                message = self.inbox.get(timeout=0.5)
            except queue.Empty:
                continue
            answer = str(message.get("text") or "")
            if not answer.strip():
                continue                       # a voice note or an attachment, not a yes or a no
            if self._is_ours(answer) or self._too_old(message):
                continue
            body = command_in(answer, require_wake=False) or answer
            choice = decision(body)
            if choice:
                self.say("Right away, sir." if choice != "deny" else "Left it alone, sir.")
                return choice
            self.inbox.put(message)      # not an answer - it's the next request; deal with it after
            break
        self.say("I'll take that as a no, sir.")
        return "deny"


# ---- one channel per JARVIS, started by the panel or by a command -------------------------------

_ACTIVE: PhoneChannel | None = None
_HANDLERS: dict = {}
_LOCK = threading.Lock()


def configure(ask, emit=None, settings=None) -> None:
    """The panel tells us how to run a request and where to log. Called once at startup."""
    _HANDLERS.update(ask=ask, emit=emit or (lambda _t: None), settings=settings)


def start(chat: str | None = None) -> str:
    """Start watching the chat. `chat` overrides (and is remembered as) the configured one."""
    global _ACTIVE
    if not _HANDLERS:
        return "Phone control needs the JARVIS panel running."
    settings = _HANDLERS.get("settings")
    where = str(chat or (settings.get("remote.chat", "") if settings else "") or "").strip()
    if not where:
        return ("Which chat should I watch, sir? Say 'watch my whatsapp +91 97700 94860' (your own "
                "number - the 'Message yourself' chat) and I'll remember it.")
    with _LOCK:
        if _ACTIVE is not None and _ACTIVE.running():
            return f"I'm already watching WhatsApp ({_ACTIVE.chat})."
        if settings is not None and where != settings.get("remote.chat", ""):
            try:
                from core.config import set_user_value
                set_user_value("remote.chat", where)      # remembered, so next time "watch my whatsapp"
            except Exception:                             # is enough
                pass
        _ACTIVE = PhoneChannel(_HANDLERS["ask"], where, emit=_HANDLERS.get("emit"),
                               require_wake=bool(settings.get("remote.require_wake", True) if settings else True),
                               voice=bool(settings.get("remote.voice_replies", True) if settings else True))
        return _ACTIVE.start()


def stop() -> str:
    with _LOCK:
        if _ACTIVE is not None and _ACTIVE.running():
            return _ACTIVE.stop()
    return "I wasn't watching WhatsApp."


def running() -> bool:
    return _ACTIVE is not None and _ACTIVE.running()


def active() -> PhoneChannel | None:
    return _ACTIVE if running() else None


def status() -> str:
    if not running():
        return "Phone control is off. Say 'watch my whatsapp' to have me answer messages from your phone."
    channel = _ACTIVE
    wake = ("in conversation - everything you send comes to me until you say 'ok dismissed'"
            if channel.session else
            ("messages starting with 'jarvis'" if channel.require_wake else "every message"))
    voice = "with a voice note" if channel.voice else "in text"
    return f"Watching {channel.chat}: I act on {wake}, and reply {voice}."
