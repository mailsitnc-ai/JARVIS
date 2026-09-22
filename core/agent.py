"""The agentic loop: pursue a goal one step at a time, deciding each step from the last result.

Unlike a fixed plan, this asks the model - after seeing what actually happened - what to do next, so it
can adapt when a step fails or returns something unexpected. Each step is a plain instruction that the
orchestrator runs through the normal machinery (an existing skill, or a freshly built one), so the agent
inherits permissions, network, package install and self-repair for free. Cloud-model only.
"""
from __future__ import annotations

from evolution_engine.analyzer import extract_json
from .oslayer import platform_key, platform_phrase

AGENT_SYSTEM = (
    "You are JARVIS carrying out a task on the user's " + platform_phrase() + ", one step at a time. You get the goal, a "
    "suggested plan, and the results of the steps already run. Decide the SINGLE next instruction to run, "
    "or finish.\n"
    "- Each instruction is one plain command JARVIS can run with a skill: e.g. 'open notepad', 'get the "
    "price of bitcoin in usd', 'search my gmail for invoices', 'calculate 3*47'. Never include a skill name.\n"
    "- To search a website, keep the word 'search': 'search youtube for lofi', 'search scholar for X'. "
    "Never turn a search into a bare topic ('super capacitors') - that won't run.\n"
    "- Use the results so far. If a step failed or returned something unexpected, adapt - try a different "
    "instruction, don't just repeat it.\n"
    "- When the goal is met (or truly can't be), set done=true and write the final answer for the user, "
    "using the results you gathered.\n"
    "Reply with ONLY a JSON object: {\"thought\":\"...\",\"done\":true|false,\"next\":\"instruction\",\"answer\":\"...\"}."
)


def next_action(llm, goal: str, plan, transcript, skill_summaries: str) -> dict:
    lines = []
    if plan:
        lines.append("Suggested plan: " + "; ".join(plan))
    for i, (instruction, result) in enumerate(transcript, 1):
        lines.append(f"Step {i}: {instruction}\nResult: {str(result)[:400]}")
    body = (f"Goal: {goal}\n\nSkills available: {skill_summaries or '(none)'}\n\n"
            + ("\n".join(lines) if lines else "(nothing run yet)")
            + "\n\nWhat is the next step?")
    text = llm.complete(messages=[{"role": "system", "content": AGENT_SYSTEM}, {"role": "user", "content": body}],
                        temperature=0.1, max_tokens=350)
    data = extract_json(text) or {}
    return {"done": bool(data.get("done")), "next": str(data.get("next") or "").strip(),
            "answer": str(data.get("answer") or "").strip(), "thought": str(data.get("thought") or "").strip()}
