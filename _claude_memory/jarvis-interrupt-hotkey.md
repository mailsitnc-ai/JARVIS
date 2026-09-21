---
name: jarvis-interrupt-hotkey
description: Ctrl+Alt+C interrupts JARVIS mid-task (cooperative cancellation)
metadata: 
  node_type: memory
  type: project
  originSessionId: 39333a9b-816b-4c6c-a177-4534fa7206f3
  modified: 2026-09-15T07:05:59.444Z
---

Added 2026-09-15 (user asked to interrupt with Ctrl+Alt+C). Cooperative cancellation - stops at the next checkpoint (between agent steps / build attempts / retries), NOT mid-network-call or mid-subprocess.

Orchestrator (`core/orchestrator.py`): `self._cancel = threading.Event()`; `interrupt()` sets it (safe from any thread, emits a line), `_cancelled()`/`_raise_if_cancelled()` (raises module-level `Cancelled`). `handle()` and `submit()` clear the event at the start and wrap the work in `try/except Cancelled -> ("Stopped.", None, "interrupted")` (build job -> "Stopped the build."). Checkpoints: `_run_agent` (top of loop + after choosing the step), `_execute` (before a repair). Evolution engine got `self.cancel_check` (set to `jarvis._cancelled` in Jarvis.__init__); `_evolve` checks it at the top of each attempt and returns `EvolutionOutcome("cancelled")`, which `_evolve_new` / `_maybe_improve` map to `raise Cancelled()`.

Hotkey plumbing: `window_manager/hotkey.py` `HotkeyListener` now takes `hotkey_id` + `name` (was a fixed class const 0x4A41) so two listeners coexist. `ui/panel.py` `run_daemon` starts a second listener for `window.interrupt_hotkey` (id 0x4A42) -> puts `("interrupt", None)` on the panel queue; `_pump` handles it by calling `jarvis.interrupt()` when busy. IPC control command `"interrupt"`; CLI `jarvis interrupt` (`_control("interrupt")`). Config default `window.interrupt_hotkey = "ctrl+alt+c"`. Doctor prints + validates it. Panel footer shows "ctrl+alt+c stop".

Verified: 123 tests pass (test_interrupt_stops_the_agent_loop, test_handle_clears_a_stale_interrupt, test_listener_hotkey_id_is_configurable); daemon registers both hotkeys; `jarvis interrupt` returns rc 0. Related: [[jarvis-agentic-loop]], [[jarvis-autonomy-and-self-improvement]], [[jarvis-windows-rebuild]].
