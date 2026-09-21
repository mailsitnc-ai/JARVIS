---
name: jarvis-broad-trigger-hijack
description: Evolved skills with single-common-word triggers hijack routing; contract now blocks them
metadata: 
  node_type: memory
  type: project
  originSessionId: 5235d300-6e27-428c-867c-70c44be16e13
  modified: 2026-09-18T11:29:57.588Z
---

2026-09-18: user reported docs/gmail/spreadsheet skills "gone" and JARVIS "stuck on super capacitors". Cause was NOT missing skills - all Google skills (google_docs, google_sheets, gmail_search, gmail_organize, drive_search, send_email) were present. The real cause: **autonomy self-evolved junk skills with single-common-word triggers that shadow the built-ins**. Worst offender `web_search_2` triggered on bare `\bsearch\b`, stealing "search my google docs/gmail/drive". Others: crypto_price on `\bprice\b`/`\busd\b`, delete_file on `\bdelete\b`, create_folder on `\bfolder\b`, open_url on `https?://`, open_file on `\bpictures\b`, claude on `\bclaude\b`, enable_tts on `\bvoice\b`, super_capacitors, web_search_results (`\brecipes\b`), open_google_scholar, generate_document.

Fix (commit after 266->267 tests):
- Deleted 9 redundant overfit evolved skills (all duplicated a built-in) via `git rm` (recoverable). Narrowed crypto_price/create_folder/delete_file to specific phrases/alternations.
- ROOT CAUSE GUARD: `core/contract.py` `_too_broad()` now REJECTS an evolved skill whose trigger reduces to a single generic word (`_BROAD_WORDS`: search/open/delete/price/file/voice/send/message/folder/...). Built-ins exempt. So a drafted or hand-edited evolved skill with a bare-word trigger fails the contract and won't load/install.
- `evolution_engine/analyzer.py` `_GENERIC_WORDS` expanded so the drafter drops those triggers and substitutes a specific keyword at draft time.
- `evolution_engine/prompts.py` teaches the model to pair verb+object or use alternations; launch_app worked-example no longer uses bare `\blaunch\b`.

If routing misbehaves for a built-in capability again, FIRST check for an evolved skill in /skills with a broad trigger shadowing it (`grep "'triggers'" skills/*.py`), then delete/narrow it. Autonomy is ON, so junk can accumulate; periodic cleanup may be needed. Related: [[jarvis-autonomy-and-self-improvement]], [[jarvis-messaging-send]].
