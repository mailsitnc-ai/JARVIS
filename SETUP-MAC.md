# JARVIS on macOS

JARVIS now runs natively on macOS as well as Windows — one codebase, platform-specific bits handled by
`core/oslayer.py`. This guide gets it running on a Mac.

## 1. Requirements

- **macOS** (Apple Silicon or Intel) with **Python 3.11+** (`python3 --version`).
- **Google Chrome** at `/Applications/Google Chrome.app` (for browser/WhatsApp control).
- Optional: **Ollama** (`brew install ollama`) if you want the offline local model.

## 2. Get the code

```bash
cd ~/Desktop
git clone <your-repo-url> JARVIS
cd JARVIS
```

## 3. Install dependencies

```bash
python3 -m pip install --user -r requirements.txt
```

`requirements.txt` covers the optional native features. The core assistant (chat, files, apps, Google,
skills) needs none of them — install only what you want:

- `opencv-python` — camera, vision, hand-gesture control
- `websocket-client` — browser/WhatsApp DevTools control
- `pyobjc-framework-Quartz` — mouse-wheel scrolling for hand-gesture control
- `pynput` — the global hotkey to summon the panel (needs Accessibility permission)

## 4. Add your API keys (you re-add these on the Mac)

```bash
python3 jarvis.py setkey groq      # paste your Groq key when prompted
python3 jarvis.py setkey gemini    # paste your Gemini key (for vision)
```

Or export them instead (these win over stored keys):
```bash
export GROQ_API_KEY=...
export GEMINI_API_KEY=...
```

Google (Docs/Gmail/Sheets/Drive) OAuth is set up the same way as on Windows, after this — see the main
README's Google section.

## 5. Run it

```bash
python3 jarvis.py doctor        # sanity check: models, skills, paths
python3 jarvis.py on            # opens the JARVIS panel
python3 jarvis.py ask "what time is it"
```

## macOS permissions to grant (System Settings ▸ Privacy & Security)

The first time each is used, macOS will prompt — or grant them ahead of time to your terminal / the
Python app:

- **Screen Recording** — for `screenshot` / "what's on my screen".
- **Camera** — for photos, vision, hand-gesture control.
- **Accessibility** — for the global hotkey (pynput) and gesture-driven scrolling.
- **Automation ▸ Terminal / Chrome** — for running programs in a window and driving Chrome.

## What's the same and what differs from Windows

Same: chat, skills, evolution, Google Docs/Gmail/Sheets, WhatsApp/Chat, camera & vision, browser
control, writing/opening files and apps, text-to-speech (`say`), screenshots (`screencapture`).

Different on macOS (by design, for now):
- The panel opens as a normal window — the Windows side-**docking** (60/40 snap) is Windows-only.
- The **global hotkey** needs `pynput` + Accessibility; without it, open the panel from the app.
- Keys are stored obfuscated in `~/Library/Application Support/JARVIS/keys.json` (chmod 600) rather than
  DPAPI-encrypted — prefer the `GROQ_API_KEY` / `GEMINI_API_KEY` env vars for anything sensitive.

Data lives in `~/Library/Application Support/JARVIS/` (config, permissions, usage, the Chrome profile).
