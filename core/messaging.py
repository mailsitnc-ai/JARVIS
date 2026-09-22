"""Parse a 'send a message' command into (recipient, is_group, message) for WhatsApp / Google Chat.

The web-app layer (core/browser.py) already sends to whatever the recipient string is - a contact
name, a group/space name, or a phone number - by searching for it. The job here is only to pull the
recipient and the body out of natural phrasings, so you can say any of:

  whatsapp mom saying I'll be late
  send a whatsapp to +1 415 555 1234 saying hi
  message John on whatsapp: running late
  send a whatsapp in the Maa Paa group - @all standup at 10
  whatsapp the family group saying dinner's ready
  google chat the Design space: shipping today
  text Priya "see you at 6"

Both group/space names and contact names are supported. A group is recognised by the words
"group"/"space" or an "in the ... group" phrasing; everything else is treated as a person.
"""
from __future__ import annotations

import re

# --- the message body -------------------------------------------------------------------------
# An explicit lead-in ("saying X", "the message is X", ": X"), a quoted span, or a " - X" dash.
_BODY_LEADIN = re.compile(
    r"(?:\bsaying\b|\bthat\s+says\b|\bwhich\s+says\b|\bthe\s+(?:message|text)\s+is\b|"
    r"\b(?:message|text)\s+is\b|\btell(?:ing)?\s+(?:them|him|her)\b|:)\s*(.+)$",
    re.IGNORECASE | re.DOTALL)
_QUOTED = re.compile(r"[\"“”'‘’](.+?)[\"“”'‘’]", re.DOTALL)
_DASH = re.compile(r"\s[-–—]\s+(.+)$", re.DOTALL)

# "type X (and then) send it/that to Y" - the body comes first, the recipient last.
_TYPE_THEN_SEND = re.compile(
    r"\btype\s+(.+?)[,\s]+(?:and\s+)?(?:then\s+)?send\s+(?:it|that|this)\s+(?:to|in)\s+(.+)$",
    re.IGNORECASE | re.DOTALL)
# "tell X (on whatsapp) that Y"
_TELL_THAT = re.compile(r"\btell\s+(.+?)\s+that\s+(.+)$", re.IGNORECASE | re.DOTALL)
# The channel named mid-sentence ("... to inaya on whatsapp ...") - not part of the name.
_CHANNEL = re.compile(r"\s*\b(?:on|via|using|in|through|over)\s+(?:whats?\s?app|google\s+chat)\b\s*",
                      re.IGNORECASE)
# A leading "open whatsapp and" / "go to whatsapp and" - an instruction, not the recipient.
_OPEN_APP = re.compile(r"^\s*(?:please\s+)?(?:open|go\s+to|launch|use)\s+(?:whats?\s?app|google\s+chat)"
                       r"\s*(?:,|and\b|then\b)*\s*", re.IGNORECASE)

# --- the recipient ----------------------------------------------------------------------------
_PHONE = re.compile(r"(\+?\d[\d\s\-().]{6,}\d)")
# "in/to/on (the) X group/space/chat"
_GROUP_IN = re.compile(r"\b(?:in|to|on|into)\s+(?:the\s+)?(.+?)\s+(?:group|space|chat|room)\b", re.IGNORECASE)
# "(the) X group/space" anywhere
_GROUP_TRAIL = re.compile(r"\b(?:the\s+)?(.+?)\s+(?:group|space|room)\b", re.IGNORECASE)
# "group/space (called/named) X" at the end
_GROUP_KW = re.compile(r"\b(?:group|space|room)\s+(?:called\s+|named\s+)?(.+?)\s*$", re.IGNORECASE)
# "<verb> X" - the name after a messaging verb, taken to the end of the prefix
_NAME_TO = re.compile(
    r"\b(?:whats?app|google\s+chat|chat|message|msg|text|dm|ping|to)\s+(.+?)\s*$", re.IGNORECASE)

# Words that are never part of a name - stripped from the ends of a captured recipient.
_FILLER = {"the", "a", "an", "to", "in", "on", "into", "via", "using", "please",
           "whatsapp", "whats", "whatapp", "app", "wa", "message", "msg", "text", "send",
           "group", "space", "room", "chat", "dm", "ping", "google", "my", "our"}


def _clean_name(name: str | None) -> str | None:
    if not name:
        return None
    name = name.strip().strip("\"'“”‘’.,:;-–— ").strip()
    tokens = name.split()
    while tokens and tokens[0].lower() in _FILLER:
        tokens.pop(0)
    while tokens and tokens[-1].lower() in _FILLER:
        tokens.pop()
    name = " ".join(tokens).strip()
    return name if 0 < len(name) <= 60 else None


def _clean_body(text: str | None) -> str | None:
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"^[-–—:\s]+", "", text)  # drop a leading dash/colon left by the split
    # Unwrap a fully quoted body.
    for open_q, close_q in (('"', '"'), ('“', '”'), ("'", "'"), ('‘', '’')):
        if len(text) >= 2 and text[0] == open_q and text.rstrip().endswith(close_q):
            text = text.strip()[1:-1]
            break
    text = text.strip().strip("\"“”‘’").strip()
    return text or None


def _split_body(request: str) -> tuple[str, str | None]:
    """Return (prefix, body): prefix is everything before the message body starts."""
    m = _BODY_LEADIN.search(request)
    if m:
        return request[:m.start()], _clean_body(m.group(1))
    q = _QUOTED.search(request)
    if q and len(q.group(1).strip()) >= 2:
        return request[:q.start()], _clean_body(q.group(1))
    d = _DASH.search(request)
    if d:
        return request[:d.start()], _clean_body(d.group(1))
    return request, None


def _extract_recipient(prefix: str, allow_phone: bool) -> tuple[str | None, bool]:
    """(recipient, is_group) from the part of the command before the message body."""
    prefix = prefix.strip()
    if allow_phone:
        p = _PHONE.search(prefix)
        if p and len(re.sub(r"\D", "", p.group(1))) >= 8:
            return "+" + re.sub(r"\D", "", p.group(1)) if p.group(1).strip().startswith("+") \
                else re.sub(r"\D", "", p.group(1)), False
    for rx in (_GROUP_IN, _GROUP_TRAIL, _GROUP_KW):  # a named group/space wins over a bare name
        m = rx.search(prefix)
        if m:
            name = _clean_name(m.group(1))
            if name:
                return name, True
    # The LAST messaging verb/"to" names the recipient: in "send a message to inaya" the name follows
    # "to", not "message". Scan every start position and keep the rightmost usable name.
    found = None
    for start in range(len(prefix)):
        m = _NAME_TO.match(prefix, start)
        if m and (start == 0 or not prefix[start - 1].isalnum()):
            name = _clean_name(m.group(1))
            if name:
                found = name
    return found, False


def parse_message_command(request: str, allow_phone: bool = True) -> tuple[str | None, bool, str | None]:
    """Parse a send command. Returns (recipient, is_group, message); any of recipient/message may be
    None when the command doesn't give it, so the caller can ask a targeted follow-up question."""
    request = _OPEN_APP.sub("", request or "")
    m = _TYPE_THEN_SEND.search(request)
    if m:
        recipient, is_group = _extract_recipient("to " + m.group(2), allow_phone)
        return recipient, is_group, _clean_body(m.group(1))
    m = _TELL_THAT.search(request)
    if m and not _BODY_LEADIN.search(request[:m.start(2)]):
        recipient, is_group = _extract_recipient("to " + _CHANNEL.sub(" ", m.group(1)), allow_phone)
        return recipient, is_group, _clean_body(m.group(2))
    prefix, body = _split_body(request)
    if body is None:
        # "message inaya on whatsapp hi how are you": the channel phrase separates name from body.
        c = _CHANNEL.search(prefix)
        if c and prefix[c.end():].strip() and prefix[:c.start()].strip():
            prefix, body = prefix[:c.start()], _clean_body(prefix[c.end():])
    recipient, is_group = _extract_recipient(_CHANNEL.sub(" ", prefix), allow_phone)
    return recipient, is_group, body
