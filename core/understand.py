"""The understanding layer: when a capable cloud model is available, it decides what the user means.

One LLM call turns any non-trivial message into a structured intent:

  chat        - conversation / a question answered in words (the model writes the reply)
  act         - one or more concrete steps to run (multi-step commands, resolved references)
  preference  - change a setting for good ("use gemini from now on", "switch to the code model")

This replaces the brittle keyword cascade for cloud mode, so multi-step commands and intent are
actually understood instead of pattern-matched. On the local 0.5B the orchestrator keeps the rules,
because a small model can't classify reliably.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from evolution_engine.analyzer import extract_json

# Settings the user is allowed to change by talking. Everything else is ignored for safety.
ALLOWED_SETTINGS = {
    "llm.fallback_order", "llm.provider", "llm.groq.model", "llm.gemini.model", "llm.ollama.model",
    "window.hotkey", "window.split", "window.jarvis_side", "evolution.enabled",
}

UNDERSTAND_SYSTEM = (
    "You are the understanding layer of JARVIS, an assistant on the user's Windows PC. Read the latest "
    "message (with the conversation for context) and classify it. Reply with ONLY a JSON object:\n"
    '{"intent": "chat|act|preference", "reply": "...", "steps": ["..."], '
    '"setting": "", "value": null, "summary": "..."}\n'
    "- intent 'chat': ONLY conversation, opinions, explanations, or a simple non-time-sensitive fact you "
    "can state in words. Put the answer in 'reply'.\n"
    "- intent 'act': the user wants something produced, made, generated, converted, computed, fetched or "
    "done (a QR code, a file, a chart, a password, live data like price/weather). Prefer 'act' whenever a "
    "real result is expected - JARVIS will build and run code to produce it, whereas describing it in chat "
    "would be fake. Put an ordered list of single, concrete instructions in 'steps'. Each step is "
    "a plain instruction the user would say (e.g. 'open google docs') - NEVER prefix it with a skill name. "
    "For a live fact, write a step that GETS the answer (e.g. 'get the current price of bitcoin in usd') "
    "so JARVIS fetches and reports it - do NOT turn it into a web search. Only use 'search the web for X' "
    "when the user explicitly says to search/look something up. Rewrite vague references "
    "('google docs' -> 'open google docs'). Split only genuinely separate actions.\n"
    "  To write a program/script/app/webpage/file and save it (and optionally open/run it), that is ONE "
    "step - keep it whole (e.g. 'write a python snake game, save it as snake.py and open it'). Never turn "
    "it into opening Notepad and typing.\n"
    "- intent 'preference': the user wants to change a setting permanently (which AI model to use, the "
    "hotkey, the split, etc.). Set 'setting' to one of the allowed keys, 'value' to the new value, and "
    "'summary' to a short human description. To change the main AI, set llm.fallback_order with the "
    "chosen provider first and the rest after (providers: groq, gemini, ollama)."
)

_EXAMPLES = [
    ("Message: what's the price of bitcoin right now",
     {"intent": "act", "steps": ["get the current price of bitcoin in usd"]}),
    ("Message: whats the weather in london",
     {"intent": "act", "steps": ["get the current weather in london"]}),
    ("Message: make an ascii qr code for hello",
     {"intent": "act", "steps": ["generate an ascii qr code for the text hello"]}),
    ("Message: find the budget file in my drive",
     {"intent": "act", "steps": ["search my google drive for the budget file"]}),
    ("Message: any emails from alice about the invoice",
     {"intent": "act", "steps": ["search my gmail for emails from alice about the invoice"]}),
    ("Message: what's a good name for a golden retriever",
     {"intent": "chat", "reply": "How about Sunny, Rusty, or Goldie?"}),
    ("Message: from now on use gemini as the main model",
     {"intent": "preference", "setting": "llm.fallback_order", "value": ["gemini", "groq", "ollama"],
      "summary": "Use Gemini as the primary model"}),
    ("Message: reverse the word banana",
     {"intent": "act", "steps": ["reverse the word banana"]}),
    ("Message: write a python program that prints the fibonacci numbers and save it to my desktop as fib.py",
     {"intent": "act", "steps": ["write a python program that prints the fibonacci numbers and save it to my desktop as fib.py"]}),
    ("Message: make me a tetris game and open it",
     {"intent": "act", "steps": ["write a tetris game, save it as tetris.py and open it"]}),
]


@dataclass
class Understanding:
    intent: str
    reply: str = ""
    steps: list = field(default_factory=list)
    setting: str = ""
    value: object = None
    summary: str = ""


def understand(llm, request: str, history, skill_summaries: str, settings_hint: str) -> Understanding:
    messages = [{"role": "system", "content": UNDERSTAND_SYSTEM}]
    for example_user, example_reply in _EXAMPLES:
        messages.append({"role": "user", "content": example_user})
        messages.append({"role": "assistant", "content": json.dumps(example_reply)})
    context = "\n".join(f"{who}: {text}" for who, text in list(history)[-6:])
    body = (f"Conversation:\n{context or '(none)'}\n\nSkills: {skill_summaries or '(none)'}\n\n"
            f"{settings_hint}\n\nMessage: {request}")
    messages.append({"role": "user", "content": body})

    text = llm.complete(messages=messages, temperature=0.1, max_tokens=400)
    data = extract_json(text) or {}

    intent = str(data.get("intent", "")).strip().lower()
    steps = [str(s).strip() for s in (data.get("steps") or []) if str(s).strip()]
    reply = str(data.get("reply") or "").strip()
    setting = str(data.get("setting") or "").strip()
    if intent not in ("chat", "act", "preference"):
        # Be forgiving about a missing/odd intent: infer from what came back.
        intent = "preference" if setting else ("act" if steps else ("chat" if reply else "act"))
    return Understanding(intent=intent, reply=reply, steps=steps, setting=setting,
                         value=data.get("value"), summary=str(data.get("summary") or "").strip())
