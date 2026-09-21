---
name: jarvis-autonomous-agenda
description: JARVIS acts on its own initiative - a scheduled agenda of tasks/goals + self-reflection loop
metadata: 
  node_type: memory
  type: project
  originSessionId: 5235d300-6e27-428c-867c-70c44be16e13
  modified: 2026-09-16T05:35:48.701Z
---

Added 2026-09-16 (user: make it "truly self-evolving" - chose ALL of: initiative over time, self-reflection, act-on-world, persistent goals). This delivers initiative + persistent goals + self-reflection (3 of 4).

`core/agenda.py` `AgendaStore` persists a task list in `%APPDATA%/JARVIS/agenda.json`: each task has id, title, prompt, kind ("task"|"reflection"), trigger (`once`{at}/`interval`{seconds, floor 30}/`daily`{time HH:MM}), enabled, next_run, last_run, last_result, runs. Pure scheduling fns `next_run(trigger, frm)`, `describe_trigger`; store methods add/remove/list/get/due(at)/mark_ran (once-tasks self-disable; interval/daily reschedule) + master `enabled`/`set_enabled`.

Daemon (`ui/panel.py` run_daemon) starts an `agenda_loop` thread: every 20s, IF `agenda.enabled()` AND autonomy is ON (unattended action can't ask permission, so it's the gate), runs each due task via `jarvis.run_agenda_task(task)`, marks it ran, posts an `agenda_done` event -> panel shows "⏱ title: result". CLI-edited agenda.json is picked up live (store reads fresh each tick).

Orchestrator: `run_agenda_task(task)` (reflection kind -> `reflect()`, else `handle(prompt)`); `reflect()` = self-review: tallies recent `skill_crashed`/`build_failed` lessons per EVOLVED skill and `evolution.improve()`s the worst one (sandbox-verified, git-committed, rollback-safe); "no evolved skills"/"all healthy" when nothing to do.

CLI `jarvis agenda [list|add|remove|run|on|off|reflect]` (add flags: --every 30m/2h/1d, --daily 08:00, --once 'YYYY-MM-DD HH:MM', --title); panel `/agenda`. `jarvis agenda reflect on` schedules self-reflection every 6h. Verified live 2026-09-16: added a 30s task, daemon ran it unprompted (runs->1, rescheduled). 143 tests pass (test_agenda.py). 

4th piece "act on the world" — LOCAL half DONE 2026-09-16: `ActionBroker.organize_dir(path)` (core/actions.py, gated write_files) sorts loose files into type subfolders (Images/Documents/Videos/Audio/Archives/Installers/Code/Other via `_FILE_CATEGORIES`/`_category_for`; never overwrites; "already tidy" when nothing loose). Builtin skill `skills/organize_files.py` ("organize/tidy my downloads/desktop/..."; default Downloads). Pairs great with agenda ("organize my downloads --every 2h"). STILL TODO: Gmail/Drive WRITE (label/archive/draft/move) - needs OAuth write scopes; Google not connected yet. Related: [[jarvis-model-usage-switching]], [[jarvis-self-editing]], [[jarvis-autonomy-and-self-improvement]], [[jarvis-google-integration]].
