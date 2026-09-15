# Evolved by JARVIS on 2026-09-15 14:13 for: get the current weather in atlanta
import os
import json
import re
from urllib.parse import urlencode

SKILL = {'name': 'weather_current', 'description': 'Retrieve the current weather conditions for a given city.', 'triggers': ['\\bweather\\b', '\\bcurrent\\s+weather\\b'], 'version': 1, 'origin': 'evolved'}


def _extract_city(request: str) -> str:
    """Return the city name found in the request or an empty string."""
    # Look for patterns like "weather in <city>" or "weather for <city>"
    m = re.search(r"\bweather\b.*?\b(in|for)\s+([a-zA-Z\s]+)", request, re.IGNORECASE)
    if m:
        return m.group(2).strip()
    # Fallback: take the last word(s) after the trigger word
    m = re.search(r"\bweather\b\s+(.*)", request, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def run(request, context):
    city = _extract_city(request)
    if not city:
        return "Please tell me which city you want the weather for."

    api_key = os.getenv("OPENWEATHER_API_KEY")
    if not api_key:
        return "Weather service is not configured (missing OPENWEATHER_API_KEY)."

    params = {
        "q": city,
        "appid": api_key,
        "units": "imperial"  # use Fahrenheit; change to 'metric' for Celsius if desired
    }
    url = f"https://api.openweathermap.org/data/2.5/weather?{urlencode(params)}"

    # Use the broker's http_request action
    response_text = context["actions"].http_request(url)

    if not response_text:
        return f"Could not retrieve weather data for {city}."

    try:
        data = json.loads(response_text)
    except json.JSONDecodeError:
        return f"Received malformed weather data for {city}."

    # Check for API errors
    if data.get("cod") != 200:
        message = data.get("message", "unknown error")
        return f"Weather service error for {city}: {message}."

    # Extract needed fields
    main = data.get("main", {})
    weather_list = data.get("weather", [])
    description = weather_list[0].get("description", "N/A") if weather_list else "N/A"
    temp = main.get("temp")
    humidity = main.get("humidity")
    wind = data.get("wind", {})
    wind_speed = wind.get("speed")

    parts = [f"Current weather in {city.title()}:"]
    if temp is not None:
        parts.append(f"Temperature: {temp}°F")
    parts.append(f"Condition: {description.capitalize()}")
    if humidity is not None:
        parts.append(f"Humidity: {humidity}%")
    if wind_speed is not None:
        parts.append(f"Wind: {wind_speed} mph")

    return " | ".join(parts)
