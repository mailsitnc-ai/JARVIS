---
name: jarvis-agentic-loop
description: "JARVIS multi-step tasks run an act-observe-adapt agent loop, not a fixed plan"
metadata: 
  node_type: memory
  type: project
  originSessionId: 39333a9b-816b-4c6c-a177-4534fa7206f3
  modified: 2026-09-15T05:48:45.294Z
---

Added 2026-09-15. `core/agent.py` (`next_action(llm, goal, plan, transcript, skill_summaries)` -> {done, next, answer, thought} via `extract_json`). Wired into `orchestrator._understand_and_act`: intent=="act" with ONE step runs directly; multi-step shows the suggested plan for one approval (ActionRequest capability "plan"; deny -> route "cancelled"), then `_run_agent(goal, plan)` loops up to `agent.max_steps` (config default 5, in `core/config.py` DEFAULTS under "agent"). Each iteration calls `next_action`, executes the chosen instruction via the normal `_run_step` (existing skill or fresh build -> inherits permissions/network/pip/self-repair), appends (instruction, result) to the transcript, and asks again. Stops on done (returns `decision["answer"]`, synthesized by the model from the transcript), on a repeated instruction (loop guard via `seen` set), or at max_steps. Route label is "agent". Cloud-only (needs `_use_planner()` true).

Live-verified 2026-09-15: "what time is it, and also calculate 3 times 47" -> plan approved -> step1 time, step2 calc -> combined answer, route agent, via groq. Rough edge: the model sometimes emits a bare skill name ("current_time") as the next instruction despite the prompt saying not to; `_clean_step` only strips a skill name that has a trailing arg, so a bare name falls through to evolution and is rescued by the duplicate-finder (correct result, slightly wasteful). Tests in tests/test_routing_and_skills.py: test_multistep_request_runs_the_agent_loop, test_agent_adapts_and_stops_when_it_repeats_itself, test_agent_respects_max_steps (111 tests pass). Related: [[jarvis-windows-rebuild]].
