# Evolved by JARVIS on 2026-09-25 14:24 for: om
import random

SKILL = {'name': 'small_talk', 'description': 'Handle brief or ambiguous user inputs with a friendly response.', 'triggers': ['\\bom\\b'], 'version': 1, 'origin': 'evolved'}


def run(request, context):
    # Trim whitespace and punctuation
    cleaned = request.strip().lower().strip(" .!?,;:")
    # If the user typed just "om" or similar short text, respond warmly
    if not cleaned or cleaned == "om":
        replies = [
            "Hey there! How can I help you today?",
            "Hello! What would you like to do?",
            "Hi! Let me know if you need anything.",
            "Hey! I'm here if you have a question."
        ]
        return random.choice(replies)
    # Otherwise, ask for clarification
    return "I’m not sure I understand. Could you tell me more?"
