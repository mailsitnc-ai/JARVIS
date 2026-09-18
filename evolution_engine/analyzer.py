"""Step 1 of evolution: analyze the missing capability into a buildable spec.

Built to survive small local models, which were measured doing two things here: copying the
prompt's placeholders verbatim, and repeating list items until they ran out of tokens. So the
prompt carries worked examples, the JSON schema caps every field's length, and the reply is
cleaned up before use: placeholder names, value triggers ("banana") and junk test inputs go.
"""
from __future__ import annotations

import json
import keyword
import re
from dataclasses import dataclass, field

from .prompts import ANALYSIS_EXAMPLES, ANALYST_SYSTEM, ANALYZE_PROMPT, fill

_STOPWORDS = frozenset(
    "a an the and or to of in on for is are was be it this that what whats how do does can could you me my "
    "i please jarvis with from at by as tell give show find get make let us your some any all word words "
    "number numbers text many much".split()
)
# Splits "count the vowels | in the word banana": the capability comes before, its values after.
_VALUE_SPLIT = re.compile(r"\s+(?:in|of|for|from|to|on|with|about|into|inside|between)\s+", re.IGNORECASE)
_PLACEHOLDER_NAMES = {"snake_case_skill_name", "skill_name", "name", "skill"} | {slug for _, ex in ANALYSIS_EXAMPLES for slug in [ex["name"]]}
_UNRELATED_PROBES = ("", "please do something unrelated", "zzqx 12345")
# A trigger that is only one of these verbs would catch unrelated requests ("generate a poem" -> password skill).
_GENERIC_WORDS = frozenset(
    "generate make create get show give find tell do run count convert calculate compute list open check "
    "random new write build search look launch start close play send post share message msg chat dm email "
    "mail call delete remove trash erase price cost value worth buy sell folder directory file files voice "
    "speak say talk read record download upload note notes doc docs document web site website page app "
    "application time date fetch display execute add".split()
)


def _plain_word(trigger: str) -> str:
    return re.sub(r"\\[bsw]|[\^$?*+()\[\]|.]", "", trigger).strip().lower()
# Small models label computations as "answer" and then get them wrong ("banana has 3 vowels: a, n, b").
# A request is treated as a plain "answer" only if it reads like a question or greeting AND asks
# for nothing to compute or do. Otherwise it becomes a skill, because a small model happily
# labels "count the vowels" an answer and then gets it wrong, and "open chrome" needs an action.
_QUESTION_START = re.compile(r"^\s*(?:who|what|whats|when|where|why|which|whose|whom|is|are|am|was|were|do|does|did|"
                             r"can|could|should|would|will|has|have|had|meaning\s+of|explain|describe|define|"
                             r"tell\s+me|how\s+(?:do|does|did|is|are|to|much|many))\b", re.IGNORECASE)
_GREETING = re.compile(r"^\s*(?:hi|hii+|hey+|hello+|yo|hiya|howdy|thanks|thank\s+you|good\s+(?:morning|evening|"
                       r"afternoon|night)|how\s+are\s+you|what'?s\s+up|sup)\b", re.IGNORECASE)
# Explicit verb endings (not bare \w*) so nouns like "movie", "computer", "calculator" and
# "creative" don't read as work. Leaning towards "skill" is fine; wrongly answering isn't.
_WORK_HINT = re.compile(
    r"\b(?:how\s+many|how\s+much|take\s+a|look\s+up|screen\s*grab|screen\s*capture|shut\s*down|"
    r"count(?:s|ed|ing)?|calculate[sd]?|calculating|compute[sd]?|computing|convert(?:s|ed|ing)?|"
    r"sort(?:s|ed|ing)?|reverse[sd]?|reversing|generate[sd]?|generating|randomi[sz]e|"
    r"add|adds|subtract|multiply|divide|sum|average|encode|decode|hash|translate[sd]?|"
    r"find|finds|finding|search(?:es|ed|ing)?|open(?:s|ed|ing)?|launch(?:es|ed|ing)?|start(?:s|ed|ing)?|"
    r"run|runs|running|execute[sd]?|play|plays|close[sd]?|screenshot|capture[sd]?|record(?:s|ed|ing)?|"
    r"type|types|click(?:s|ed)?|press(?:es|ed)?|scroll|send|sends|email|emails|message|post|posts|"
    r"download(?:s|ed|ing)?|upload(?:s|ed|ing)?|install(?:s|ed|ing)?|uninstall|set|change[sd]?|toggle|"
    r"enable|disable|mute|lock|restart|reboot|create[sd]?|creating|make|makes|making|delete[sd]?|deleting|"
    r"remove[sd]?|removing|rename[sd]?|copy|copies|move|moves|moving|save[sd]?|saving|write|writes|"
    r"edit|edits|append|list|lists|listing|"
    r"say|says|speak|speaks|read\s+(?:it|this|that|me|aloud|out)|pronounce|announce|narrate|dictate|"
    r"remind|schedule|pause|resume|turn\s+(?:on|off|up|down))\b",
    re.IGNORECASE)


def is_answerable(request: str) -> bool:
    """Looks like a question or greeting, so a plain text answer is appropriate."""
    return bool(_GREETING.search(request) or _QUESTION_START.search(request) or request.strip().endswith("?"))


def looks_actionable(request: str) -> bool:
    """True when the message asks JARVIS to *do* or *compute* something (an action/compute verb)."""
    return bool(_WORK_HINT.search(request))


# Only genuine self-code-editing is refused. "Give yourself the ability to X" is NOT here on purpose:
# that's the whole point of a self-evolving assistant, so it flows to building a skill for X.
_META = re.compile(
    r"\b(?:edit|change|modify|rewrite|update|patch|alter|delete|remove|access|show\s+me)\s+"
    r"(?:your|the\s+jarvis|its)\s+(?:own\s+)?(?:code|source(?:\s*code)?|prompt|config|configuration|"
    r"settings|files?|core|programming|internals?)\b|"
    r"\b(?:rewrite|reprogram|hack|jailbreak)\s+your\s*self\b|"
    r"\bchange\s+your\s+(?:system\s+)?prompt\b",
    re.IGNORECASE)


def is_meta_request(request: str) -> bool:
    return bool(_META.search(request))

# Capped lengths keep a small model from looping on list items until num_predict runs out.
ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["skill", "answer"]},
        "name": {"type": "string", "maxLength": 40},
        "description": {"type": "string", "maxLength": 160},
        "triggers": {"type": "array", "items": {"type": "string", "maxLength": 60}, "minItems": 1, "maxItems": 3},
        "test_inputs": {"type": "array", "items": {"type": "string", "maxLength": 100}, "minItems": 1, "maxItems": 3},
        "plan": {"type": "string", "maxLength": 300},
    },
    "required": ["kind", "name", "description", "triggers", "test_inputs", "plan"],
}


@dataclass
class CapabilitySpec:
    kind: str  # "skill" or "answer"
    name: str
    description: str
    triggers: list[str]
    test_inputs: list[str] = field(default_factory=list)
    plan: str = ""
    side_effects: bool = False


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")[:40].strip("_")
    if not slug:
        return ""
    if not slug[0].isalpha():
        slug = "skill_" + slug
    if keyword.iskeyword(slug):
        slug += "_skill"
    return slug


def _keywords(text: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z]+", text.lower()) if w not in _STOPWORDS and len(w) > 2]
    return list(dict.fromkeys(words))


def capability_part(request: str) -> str:
    """'count the vowels' in 'count the vowels in the word banana'."""
    head = _VALUE_SPLIT.split(request.strip(), maxsplit=1)[0]
    return head if _keywords(head) else request


def keyword_trigger(request: str) -> str:
    words = _keywords(capability_part(request)) or _keywords(request)
    words = [w for w in words if w not in _GENERIC_WORDS] or words
    if not words:
        return re.escape(request.strip().lower()[:40])
    return r"\b" + re.escape(max(words, key=len)) + r"\b"


def _compiles(pattern: str) -> bool:
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error:
        return False
    return True


def extract_json(text: str) -> dict | None:
    cleaned = re.sub(r"```(?:json)?", "", text)
    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(cleaned[index:])
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def clean_triggers(raw, request: str) -> list[str]:
    head = capability_part(request)
    candidates = [t for t in raw if isinstance(t, str) and t.strip() and _compiles(t)]
    candidates = [t for t in candidates if not any(re.search(t, probe, re.IGNORECASE) for probe in _UNRELATED_PROBES)]
    matching = [t for t in candidates if re.search(t, request, re.IGNORECASE)]
    # Prefer triggers about the capability; ones matching only the values ("banana") would overfit.
    about_capability = [t for t in matching if re.search(t, head, re.IGNORECASE)]
    triggers = about_capability or matching or [keyword_trigger(request)]
    specific = [t for t in triggers if _plain_word(t) not in _GENERIC_WORDS]
    if not specific:
        specific = [keyword_trigger(request)]
    return list(dict.fromkeys(specific))[:3]


def heuristic_spec(request: str) -> CapabilitySpec:
    words = _keywords(capability_part(request))
    return CapabilitySpec(
        kind="skill",
        name=slugify("_".join(words[:3])) or "custom_skill",
        description=request.strip()[:200],
        triggers=[keyword_trigger(request)],
        test_inputs=[request],
        plan="Take the value to work on from the request (quoted text, a number, or else the last word) "
             "and compute the result with the Python standard library.",
    )


def normalize_spec(data: dict, request: str) -> CapabilitySpec:
    # An action/compute verb means a skill; anything else is a plain answer. This is deterministic on
    # purpose: a 0.5B model can't reliably classify, but "does it contain a work verb?" it doesn't need to.
    kind = "skill" if _WORK_HINT.search(request) and not _GREETING.search(request) else "answer"
    fallback = heuristic_spec(request)

    name = slugify(data.get("name", ""))
    if not name or name in _PLACEHOLDER_NAMES or "snake_case" in name:
        name = fallback.name

    extra_inputs = [t.strip() for t in (data.get("test_inputs") or [])
                    if isinstance(t, str) and len(t.split()) >= 2]  # drops junk like "banana" or "request"
    inputs = list(dict.fromkeys([request.strip()] + extra_inputs))[:3]
    plan = str(data.get("plan") or "").strip()

    return CapabilitySpec(
        kind=kind,
        name=name,
        description=str(data.get("description") or fallback.description).strip()[:300],
        triggers=clean_triggers(data.get("triggers") or [], request),
        test_inputs=inputs,
        plan=(plan if len(plan) > 15 else fallback.plan)[:600],
        side_effects=bool(data.get("side_effects", False)),
    )


def analyze(llm, request: str, skill_summaries: str) -> CapabilitySpec:
    if _GREETING.search(request):
        # Chit-chat: answer directly, no analysis LLM call needed.
        return CapabilitySpec(kind="answer", name="chit_chat", description="Reply to a greeting.",
                              triggers=[], test_inputs=[request], plan="")
    messages = [{"role": "system", "content": ANALYST_SYSTEM}]
    for example_request, example_reply in ANALYSIS_EXAMPLES:
        messages.append({"role": "user", "content": fill(ANALYZE_PROMPT, request=example_request,
                                                         skills="- calculator: Evaluate arithmetic")})
        messages.append({"role": "assistant", "content": json.dumps(example_reply)})
    messages.append({"role": "user", "content": fill(ANALYZE_PROMPT, request=request, skills=skill_summaries or "(none)")})
    text = llm.complete(messages=messages, temperature=0.1, max_tokens=350, json_schema=ANALYSIS_SCHEMA)
    data = extract_json(text)
    return normalize_spec(data, request) if data else heuristic_spec(request)
