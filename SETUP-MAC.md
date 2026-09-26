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

The **core assistant needs nothing** — it's standard-library only. So you can skip straight to step 5
and run it. To add camera/vision and browser/WhatsApp control (prebuilt wheels, no compiler):

```bash
python3 -m pip install --user -r requirements.txt
```

Optional extras that may need to compile (`requirements-extras.txt`: gesture mouse-scroll via pyobjc,
global hotkey via pynput) only install cleanly on a Python that ships prebuilt wheels for your macOS —
the **python.org** or **Homebrew** Python 3.11+, not the bare Command Line Tools Python. If
`pip install -r requirements-extras.txt` fails to build, it's safe to skip; you just lose gesture-scroll
and the summon hotkey. A newer Python also gives you the Tk panel window.

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

## 6. Always on, so your phone can reach it

JARVIS answers your WhatsApp and serves the call page from *this* Mac, so the phone side is only
alive while the Mac's JARVIS is. One command makes that stop being your job:

```bash
python3 jarvis.py startup enable     # jarvis startup disable / status
```

That installs a launchd agent (`~/Library/LaunchAgents/com.jarvis.keeper.plist`) which runs
`jarvis keeper` - a loop that asks JARVIS every 15 seconds whether it's there and starts it when it
isn't. So JARVIS comes up when you log in, comes back if it crashes at 3am, and gets replaced if it
ever wedges (a wrapper left with a dead engine inside is cleared out after about a minute). It
launches `JARVIS.app` when that bundle exists, so macOS keeps the camera/microphone grants attached
to JARVIS; if the app won't start twice in a row it falls back to running the engine directly.
Keeper log: `~/Library/Logs/JARVIS-keeper.log`.

Inside JARVIS, the same idea covers the phone channels (`core/keeper.py`, `watch_services`): the call
page, its address on the internet and the WhatsApp watcher are started at boot and put back within
half a minute whenever one falls over - sleep drops tunnels and closes Chrome, and that used to leave
the phone talking to nothing until someone restarted JARVIS by hand.

What this cannot do: a Mac that is **asleep or shut down** serves nothing, and an incoming call can't
wake it from outside the house. The call page now says so plainly and keeps retrying, so it connects
by itself once the Mac is back. If you want JARVIS reachable all night, stop the Mac sleeping while
it's plugged in (needs your password):

```bash
sudo pmset -c sleep 0 disablesleep 0
```

And if you want it up after a reboot without logging in by hand, turn on automatic login in System
Settings > Users & Groups - a login agent only runs once you're logged in.

## macOS permissions to grant (System Settings ▸ Privacy & Security)

The first time each is used, macOS will prompt — or grant them ahead of time to your terminal / the
Python app:

- **Screen Recording** — for `screenshot` / "what's on my screen".
- **Microphone** — for voice: say "Jarvis, …" (speech is recognised on the Mac with faster-whisper;
  nothing that isn't addressed to JARVIS is kept). Turn on with `/voice on` or `jarvis config --set voice.enabled=true`.
- **Camera** — for photos, vision, hand-gesture control. For accurate hand tracking on Apple Silicon, install
  `mediapipe==0.10.21` and the `hand_landmarker.task` model (exact commands in `requirements-extras.txt`);
  without them hand control falls back to skin-colour detection.
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
