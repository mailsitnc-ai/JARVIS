"""WhatsApp without a browser: Meta's own Cloud API.

The other WhatsApp channel (core/remote.py) drives WhatsApp Web in Chrome on your Mac, which means it
only lives while the Mac does. This one is the official route: Meta posts each message you send to a
web address, JARVIS answers it, and the reply goes back over the same API. No Chrome, no phone
pairing, nothing to keep logged in - so it runs perfectly well on a machine with no screen.

What it needs (all from the Meta app you set up once):
    whatsapp.token          the access token for the app
    whatsapp.phone_id       the phone number ID the messages are sent from
    whatsapp.allowed        the numbers allowed to command JARVIS - yours
    whatsapp.verify         any word you choose; Meta echoes it back when it hooks up

The token is a credential, so it is kept where the other keys are (the keystore or the environment),
never in the config file:  jarvis setkey whatsapp
"""
from __future__ import annotations

import json
import logging
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("jarvis.wacloud")

GRAPH = "https://graph.facebook.com/v21.0"
MAX_REPLY = 3900              # WhatsApp refuses a body over 4096 characters
SEEN = 400                    # message ids remembered, so a retry isn't answered twice


def digits(number: str) -> str:
    """Just the digits, so +91 97700 94860 and 919770094860 are the same person."""
    return re.sub(r"\D", "", str(number or ""))


def parse(payload: dict) -> list[dict]:
    """The messages inside one webhook call: [{id, from, text}].

    Meta's shape is deep and changes at the edges, and the same call also carries delivery receipts
    and status updates - which are not messages and must not be answered."""
    out = []
    for entry in (payload or {}).get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            for message in value.get("messages", []) or []:
                body = ""
                kind = message.get("type")
                if kind == "text":
                    body = ((message.get("text") or {}).get("body") or "").strip()
                elif kind in ("button", "interactive"):
                    inner = message.get(kind) or {}
                    body = str(inner.get("text") or (inner.get("button_reply") or {}).get("title")
                               or (inner.get("list_reply") or {}).get("title") or "").strip()
                if not body:
                    continue         # a photo, a sticker, a voice note: nothing to act on yet
                out.append({"id": str(message.get("id") or ""),
                            "from": digits(message.get("from")),
                            "text": body})
    return out


class CloudChannel:
    """Answers WhatsApp messages that Meta delivers, with no browser in sight."""

    def __init__(self, ask, token: str, phone_id: str, allowed=(), verify: str = "jarvis",
                 emit=None, send=None):
        self.ask = ask
        self.token = str(token or "")
        self.phone_id = str(phone_id or "")
        self.allowed = {digits(number) for number in (allowed or []) if digits(number)}
        self.verify = str(verify or "jarvis")
        self.emit = emit or (lambda _text: None)
        self._send = send or self._post
        self.seen: list[str] = []
        self._lock = threading.Lock()

    # ---- the hookup Meta does once ---------------------------------------------------------------
    def challenge(self, params: dict) -> tuple[int, str]:
        """Meta proves the address is yours before it will send anything: it asks for a word you
        chose and expects it echoed back."""
        if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == self.verify:
            return 200, str(params.get("hub.challenge") or "")
        return 403, "no"

    # ---- a message arrives -----------------------------------------------------------------------
    def allow(self, number: str) -> bool:
        """Anyone can message a published number. Only you may command JARVIS."""
        return not self.allowed or digits(number) in self.allowed

    def fresh(self, ident: str) -> bool:
        """Meta re-delivers a message when it doesn't get a prompt 200, so the same request can
        arrive several times. It must only be carried out once."""
        if not ident:
            return True
        with self._lock:
            if ident in self.seen:
                return False
            self.seen.append(ident)
            del self.seen[:-SEEN]
        return True

    def deliver(self, payload: dict) -> int:
        """Answer everything in one webhook call. Returns how many were answered."""
        answered = 0
        for message in parse(payload):
            if not self.allow(message["from"]):
                log.info("ignored a message from %s", message["from"])
                continue
            if not self.fresh(message["id"]):
                continue
            answered += 1
            threading.Thread(target=self._answer, args=(message,), daemon=True).start()
        return answered

    def _answer(self, message: dict) -> None:
        self.emit(f"whatsapp: {message['text']}")
        try:
            reply = str(self.ask(message["text"]) or "").strip() or "Done, sir."
        except Exception as exc:
            log.exception("a request from WhatsApp failed")
            reply = f"That went wrong, sir: {exc}"
        self.say(reply, to=message["from"])

    # ---- talking back ----------------------------------------------------------------------------
    def say(self, text: str, to: str) -> bool:
        body = str(text or "").strip()
        if not body:
            return False
        if len(body) > MAX_REPLY:
            body = body[:MAX_REPLY - 1] + "…"
        return self._send(to, body)

    def _post(self, to: str, body: str) -> bool:
        request = urllib.request.Request(
            f"{GRAPH}/{self.phone_id}/messages",
            data=json.dumps({"messaging_product": "whatsapp", "to": digits(to),
                             "type": "text", "text": {"body": body}}).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(request, timeout=20) as answer:
                return 200 <= answer.status < 300
        except urllib.error.HTTPError as exc:
            log.warning("WhatsApp refused the reply: %s %s", exc.code,
                        exc.read()[:300].decode("utf-8", "replace"))
        except (urllib.error.URLError, OSError) as exc:
            log.warning("couldn't reach WhatsApp: %s", exc)
        return False


# ---- the module-level channel the server uses ------------------------------------------------------

_ACTIVE: CloudChannel | None = None


def configure(ask, settings, emit=None) -> CloudChannel | None:
    """Build the channel from your settings, or say why it can't be built."""
    global _ACTIVE
    from core import keystore

    token = keystore.get_key("whatsapp") or ""
    phone_id = str(settings.get("whatsapp.phone_id", "") or "")
    if not token or not phone_id:
        return None
    allowed = settings.get("whatsapp.allowed", []) or []
    if isinstance(allowed, str):
        allowed = [allowed]
    _ACTIVE = CloudChannel(ask, token=token, phone_id=phone_id, allowed=allowed,
                           verify=str(settings.get("whatsapp.verify", "jarvis") or "jarvis"),
                           emit=emit)
    return _ACTIVE


def current() -> CloudChannel | None:
    return _ACTIVE
