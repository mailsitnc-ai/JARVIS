---
name: jarvis-messaging-send
description: JARVIS sends WhatsApp/Google Chat by contact OR group/space name; message_send always confirms
metadata: 
  node_type: memory
  type: project
  originSessionId: 5235d300-6e27-428c-867c-70c44be16e13
  modified: 2026-09-18T13:00:20.298Z
---

WhatsApp + Google Chat sending (SENSITIVE `message_send` - always asks first, showing recipient + full body, even under autonomy). The web layer (core/browser.py `whatsapp_send`/`chat_send`) searches WhatsApp Web / chat.google.com for whatever recipient string it's given, so a **contact name, a group/space name, or a phone number** all work (phone uses WhatsApp's send deep link).

KASPERSKY BLOCKS WHATSAPP WEB (found 2026-09-18): On the user's machine WhatsApp Web hangs forever on the splash (readyState stuck 'loading'/'interactive', progress bar 0/100, no QR). Root cause diagnosed via CDP: requests to `static.whatsapp.net` (WA's JS/font CDN) never finish, and `gc.kis.v2.scr.kaspersky-labs.com` is in the request chain = **Kaspersky (`avp`/`avpui`) "Scan encrypted connections" is MITM-ing HTTPS and stalling WA's own code download**. Not RAM, not profile, not persistent-storage. FIX IS IN KASPERSKY, not our code: add `web.whatsapp.com`, `*.whatsapp.net`, `*.whatsapp.com` to Kaspersky trusted addresses / encrypted-connection-scanning exclusions, OR turn off "Scan encrypted connections", OR pause protection while linking (but sends also load WA each time, so exclusions are the durable fix). Secondary real issue also handled in code: WA Web needs persistent storage or it hangs (`acquire-persistent-storage-denied`) - `ChromeController._prepare_whatsapp()` now grants `durableStorage` via CDP `Browser.grantPermissions` + `Network.setBypassServiceWorker` before every WA load (called in whatsapp_login/whatsapp_send). JARVIS's Chrome debug profile: `%APPDATA%\JARVIS\chrome-debug` (persistent; a corrupted one can be moved aside to reset).

ARCHITECTURE: JARVIS drives WhatsApp Web as the USER's own linked device (QR scan in JARVIS's dedicated persistent Chrome profile, APPDATA/JARVIS/chrome-debug). So it sends AS the user - it can reach any group/contact the user is already in; it does NOT and CANNOT be a separate WhatsApp identity or message people/groups the user isn't in (WhatsApp allows that for nobody). No official personal-send API exists, so DOM automation is the only way. One-time linking: **"log in to WhatsApp"** (skill whatsapp `_LOGIN`, broker `whatsapp_login` gated 'browser', `ChromeController.whatsapp_login` waits for the scan). Session persists (~weeks), so after one scan sends are quick and reuse the open tab (`_wa_logged_in`). If sends say "isn't linked", tell the user to say "log in to WhatsApp" once. Business/Cloud API was considered but rejected: it needs approved templates + opt-in and can't freely message groups.

2026-09-18: added group/space name sending + robust parsing. New `core/messaging.py` `parse_message_command(request, allow_phone)` -> (recipient, is_group, message). Handles many phrasings: recipient via "in the X group", "the X group", "group X", "<verb> X", or a phone; body via saying / that says / the message is / colon / " - dash " / quotes. Used by `skills/whatsapp.py` and `skills/google_chat.py` (both now version 2).

ROUTING HAZARDS FIXED (all 2026-09-18):
1. Evolved `skills/speak_text.py` (TTS) had a bare `\bsaying\b` trigger that hijacked any messaging command ("whatsapp the group saying X" was read aloud instead of sent). Tightened to real speak-aloud intent (starts with say/speak/saying, or an aloud/out-loud/text-to-speech cue).
2. `core/refs.py` reference resolver was rewriting words INSIDE the message body: "...group - @all this is test" had "this" replaced with a focused URL, which then routed to open_url. Now the resolver protects a send/compose command's literal body (`_body_start`) and treats demonstratives ("this is a test") as non-references.
3. A body with commas ("...saying hi, everyone") made the orchestrator's multi-step splitter skip the whatsapp trigger; the LLM rephrased the step without "whatsapp", nothing matched, and evolution built a FAKE `send_chat_message` skill (deleted) that only said "Message queued". Fix: `Jarvis._direct_message_route` in core/orchestrator.py force-routes to whatsapp/google_chat when a request has BOTH a channel word AND a body lead-in, before the splitter/evolution.

If a messaging command misbehaves again, check in this order: speak_text triggers, refs body-protection, then `_direct_message_route`. Related: [[jarvis-browser-dom-control]], [[jarvis-conversation-context]].
