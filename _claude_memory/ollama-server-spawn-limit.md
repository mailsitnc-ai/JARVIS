---
name: ollama-server-spawn-limit
description: "In Claude's tool environment here, processes launched from tool shells can't spawn children; the Ollama tray app fails to start its server"
metadata: 
  node_type: memory
  type: reference
  originSessionId: a540b646-f42b-4abc-bfb7-2dd2490e425e
  modified: 2026-09-14T04:17:12.169Z
---

Observed 2026-09-14: a PowerShell tool call once failed with `EPERM: operation not permitted, uv_spawn powershell.exe`, and the Ollama tray app ("ollama app.exe") launched via Start-Process from a tool shell logged "starting ollama server" but the server never came up (empty server.log, nothing on port 11434).

Workaround that worked: run `ollama.exe serve` directly as a background tool task, with the OLLAMA_* user variables copied into that shell's environment first. This is a limit of the tool sandbox, not of Ollama: at a normal Windows sign-in the tray app starts the server fine, and JARVIS's own autostart (`core/llm_router.py`) also launches `ollama serve` with the registry OLLAMA_* variables.

Related: [[jarvis-windows-rebuild]], [[windows-laptop-specs]].
