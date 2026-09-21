---
name: build-from-pasted-spec
description: "User wants building to start from the spec they pasted, not long environment probing; Mac references mean Windows"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: a540b646-f42b-4abc-bfb7-2dd2490e425e
  modified: 2026-09-13T16:35:52.880Z
---

When the user pastes old chats as a spec, start building quickly. They interrupted repeated environment-probing commands and said "the current folder is blank, use the chats that I gave". Anywhere the old material says macOS/Mac, build the Windows-native equivalent.

**Why:** the user found step-by-step probing slow and wanted the replica built.
**How to apply:** do one quick batched environment check at most, then write code. Ask only for permission-gated actions such as downloads. Related: [[jarvis-windows-rebuild]].
