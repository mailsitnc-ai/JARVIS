---
name: windows-laptop-specs
description: "The user's Windows laptop hardware, locale and Python install facts that constrain JARVIS"
metadata: 
  node_type: memory
  type: user
  originSessionId: a540b646-f42b-4abc-bfb7-2dd2490e425e
  modified: 2026-09-13T16:35:42.757Z
---

Windows 10 Pro laptop (checked 2026-09-13): 3.9 GB RAM (often under 1 GB free), Intel i5-3210M (2 cores, 2012), HD 4000 GPU, 1280x800 screen (work area 1280x760), ~83 GB free disk. System messages are in French; keyboard layout is fr-FR AZERTY (Ctrl+Alt = AltGr).

Python 3.13.15 installed per-user via winget at `%LOCALAPPDATA%\Programs\Python\Python313\python.exe`, not on PATH. `C:\Program Files\Python313` is a broken leftover with no python.exe. No Ollama installed. git and winget are available.

Local LLMs are not viable here; see [[jarvis-windows-rebuild]].
