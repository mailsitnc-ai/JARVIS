---
name: jarvis-browser-dom-control
description: "JARVIS real Chrome DOM control via DevTools (navigate/read/click/type/JS), gated by 'browser'"
metadata: 
  node_type: memory
  type: project
  originSessionId: 5235d300-6e27-428c-867c-70c44be16e13
  modified: 2026-09-18T05:58:20.276Z
---

Added 2026-09-17 (user: "give JARVIS access to chrome DOM, surpass our limits"). Commit `8807f30`.

Plain open-a-URL can't read/act on a page, so this adds genuine DOM control over the **Chrome DevTools Protocol (CDP)** — chosen over Selenium/Playwright because it's light on the 3.9GB/2012-i5 laptop (no browser downloads).

- `core/browser.py` `ChromeController(port, profile_dir, chrome_exe, installer)`: finds Chrome (which/App Paths/known dirs), launches **its own** Chrome window with `--remote-debugging-port` + a dedicated profile (`%APPDATA%/JARVIS/chrome-debug`) so it never disturbs the user's normal Chrome, attaches over a websocket, and exposes `navigate`, `text` (innerText), `click` (by visible text or CSS selector), `type_text` (native value setter so React inputs work) + submit, `evaluate` (run JS), `screenshot` (Page.captureScreenshot). Only third-party dep is `websocket-client`, auto-installed on first use (gated by `packages`) like opencv. `_cmd` matches response ids and skips CDP events.
- New permission capability **`browser`** in `core/permissions.py` (NOT in SENSITIVE — autonomy allows it).
- `ActionBroker.browser_open/read/click/type/run_js/screenshot` (each gated `browser`, lazy-import ChromeController so actions.py stays sandbox-loadable); open/screenshot record the URL/image into the conversation focus. Broker gained `browser_port`/`browser_profile` ctor args.
- Skill `skills/browser_control.py`: "browse to X", "read/summarise the page", "click the <text> button", "type X in the search box and press enter", "screenshot the page". Declines (returns None) when nothing matches.
- Config `window.debug_port` (default 9222), wired through orchestrator `_context`.

Messaging (2026-09-18, commit `39e4040`): WhatsApp + Google Chat sending, browser-driven (no personal API). New SENSITIVE capability **`message_send`** (autonomy never blanket-allows) -> always confirms showing app+recipient+text. `ChromeController.whatsapp_send` (reliable `web.whatsapp.com/send?phone=` deep link for numbers; best-effort sidebar search for names) + `chat_send` (chat.google.com search+open+type+send), with `_wait_for` polling and fallback-heavy DOM selectors (module-level `_WA_*`/`_GC_*` JS). `ActionBroker.send_message(app,to,message)`. Skills `whatsapp.py` ("whatsapp mom saying ...", "send a whatsapp to +1... saying ...") and `google_chat.py`. Needs the user logged into WhatsApp Web (QR) / Chat inside JARVIS's Chrome; selectors may need fix_code tweaks if sites change. Also API-based: Google **Docs** (`google_docs` skill: search/read/create-formatted/append, commit `0030d4c`) and **Sheets** (`google_sheets`: search/read/create/append-row/set-cell, `658253b`) - see [[jarvis-google-integration]].

First use launches a **separate JARVIS Chrome window** (log into sites there once; the profile persists). Chrome confirmed present at `C:\Program Files\Google\Chrome\Application\chrome.exe`. Core change → needs a JARVIS **restart** to take effect (skills hot-reload, core does not). 189 tests pass (+15, controller tested with a mocked websocket). Related: [[jarvis-conversation-context]], [[jarvis-camera-vision]].
