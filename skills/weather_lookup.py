# Evolved by JARVIS on 2026-09-15 13:48 for: get the current temperature in atlanta
import os
import json
import re
from urllib.parse import quote_plus

SKILL = {'name': 'weather_lookup', 'description': 'Retrieve the current temperature for a specified city.', 'triggers': ['\\bcurrent\\s+temperature\\b'], 'version': 1, 'origin': 'evolved'}

# OpenWeatherMap endpoint (metric units)
BASE_URL = "https://api.openweathermap.org/data/2.5/weather?units=metric&appid={key}&q={city}"


def _extract_city(request: str) -> str:
    """
    Try to find a city name in the request.
    Looks for patterns like "in <city>" or "for <city>".
    """
    match = re.search(r"\b(?:in|for)\s+([A-Za-z\s]+)", request, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    # fallback: last word(s) after the trigger phrase
    words = request.split()
    if words:
        return words[-1]
    return ""


def run(request, context):
    city = _extract_city(request)
    if not city:
        return "Please tell me which city you want the temperature for."

    api_key = os.getenv("OPENWEATHER_API_KEY")
    if not api_key:
        return "Weather lookup requires an OpenWeatherMap API key set in the OPENWEATHER_API_KEY environment variable."

    url = BASE_URL.format(key=api_key, city=quote_plus(city))
    response_text = context["actions"].http_request(url)

    if not response_text:
        return f"Unable to retrieve weather data for {city}."

    try:
        data = json.loads(response_text)
    except json.JSONDecodeError:
        return f"Received an unexpected response while looking up weather for {city}."

    # OpenWeatherMap returns a field "cod" that is 200 on success
    if data.get("cod") != 200:
        message = data.get("message", "unknown error")
        return f"Could not get weather for {city}: {message}."

    temp = data.get("main", {}).get("temp")
    if temp is None:
        return f"Weather data for {city} does not contain temperature information."

    return f"The current temperature in {city.title()} is {temp:.1f}°C."
