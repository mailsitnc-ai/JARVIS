# Evolved by JARVIS on 2026-09-15 22:24 for: Claude
SKILL = {'name': 'claude', 'description': 'Provide a brief description of Claude, the AI model by Anthropic.', 'triggers': ['\\bclaude\\b'], 'version': 1, 'origin': 'evolved'}


def run(request, context):
    # The skill is triggered for any request containing the word "claude".
    # We simply return a concise description.
    description = (
        "Claude is an advanced AI language model created by Anthropic. "
        "It is designed to be helpful, harmless, and honest, and it can understand and generate natural language, "
        "assist with reasoning tasks, and follow user instructions while adhering to safety guidelines."
    )
    return description
