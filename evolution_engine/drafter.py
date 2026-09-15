"""Step 2 of evolution: draft (or redraft) the skill's Python source with the LLM.

Every draft request is preceded by a worked example turn (DRAFT_EXAMPLE), which shows a small
model how to pull the value out of a natural-language request instead of searching for keywords.
"""
from __future__ import annotations

import json
import re

from .analyzer import CapabilitySpec
from .prompts import CODER_SYSTEM, DRAFT_EXAMPLES, DRAFT_PROMPT, RETRY_SUFFIX, fill


def extract_code(text: str) -> str:
    blocks = re.findall(r"```(?:python|py)?[ \t]*\r?\n(.*?)```", text, re.DOTALL)
    if blocks:
        with_run = [b for b in blocks if "def run" in b]
        return max(with_run or blocks, key=len).strip() + "\n"
    return text.strip() + "\n"


def _draft_prompt(description: str, plan: str, request: str, name: str, triggers: list[str]) -> str:
    return fill(DRAFT_PROMPT, description=description, plan=plan or "Use the Python standard library.",
                request=request, name=name, triggers=json.dumps(triggers))


def draft_skill(llm, spec: CapabilitySpec, request: str, feedback: str | None = None,
                previous_code: str | None = None, temperature: float = 0.2, lessons: list[str] | None = None) -> str:
    prompt = _draft_prompt(spec.description, spec.plan, request, spec.name, spec.triggers)
    if lessons:
        prompt += "\n\nLessons from past attempts, don't repeat these mistakes:\n- " + "\n- ".join(lessons[-4:])
    if feedback:
        prompt += fill(RETRY_SUFFIX, feedback=feedback[-3000:], code=(previous_code or "")[-6000:])
    messages = [{"role": "system", "content": CODER_SYSTEM}]
    for example in DRAFT_EXAMPLES:
        messages.append({"role": "user", "content": _draft_prompt(example["description"], example["plan"],
                                                                  example["request"], example["name"], example["triggers"])})
        messages.append({"role": "assistant", "content": "```python\n" + example["code"] + "```"})
    messages.append({"role": "user", "content": prompt})
    # A skill is small; a cap keeps a rambling small model from spending minutes on one draft.
    text = llm.complete(messages=messages, temperature=temperature, max_tokens=900)
    return extract_code(text)
