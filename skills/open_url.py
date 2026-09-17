# Evolved by JARVIS on 2026-09-16 20:36 for: https://docs.google.com/document/
import re

SKILL = {'name': 'open_url', 'description': 'Open a specified web address in the default browser.', 'triggers': ['https?://'], 'version': 1, 'origin': 'evolved'}


def run(request, context):
    # Find the first URL in the request
    match = re.search(r"https?://[^\s]+", request)
    if not match:
        return "I couldn't find a URL to open. Please provide a valid web address."
    url = match.group(0)
    # Use the broker to open the URL
    result = context["actions"].open_url(url)
    # The broker may return a message or None; craft a friendly response
    if result:
        return result
    return f"Opened {url} in your default browser."
