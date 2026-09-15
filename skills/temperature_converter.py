"""Convert temperatures between Celsius, Fahrenheit and Kelvin."""
import re

SKILL = {
    "name": "temperature_converter",
    "description": "Convert a temperature between Celsius, Fahrenheit and Kelvin.",
    "triggers": [
        r"\b(?:celsius|centigrade|fahrenheit|kelvin)\b",
        r"-?\d+(?:[.,]\d+)?\s*°\s*[cfk]?\b",
        r"\b(?:convert|what\s+is|how\s+much\s+is)\s+-?\d+(?:[.,]\d+)?\s*(?:degrees?\s*)?[cfk]\b",
    ],
    "version": 1,
    "origin": "builtin",
}

UNITS = {"c": "c", "celsius": "c", "centigrade": "c", "f": "f", "fahrenheit": "f", "k": "k", "kelvin": "k"}
SYMBOLS = {"c": "°C", "f": "°F", "k": "K"}
_UNIT = r"(celsius|centigrade|fahrenheit|kelvin|c|f|k)"


def _to_celsius(value, unit):
    return value if unit == "c" else (value - 32) * 5 / 9 if unit == "f" else value - 273.15


def _from_celsius(value, unit):
    return value if unit == "c" else value * 9 / 5 + 32 if unit == "f" else value + 273.15


def _fmt(value):
    return f"{value:.2f}".rstrip("0").rstrip(".")


def run(request, context):
    text = request.lower().replace(",", ".")
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*°?\s*(?:degrees?\s*)?" + _UNIT + r"\b", text)
    if not match:
        return None if not re.search(r"\d", text) else "Give me a temperature with its unit, for example: convert 30 C to F."
    value, source = float(match.group(1)), UNITS[match.group(2)]
    wanted = re.search(r"\b" + _UNIT + r"\b", text[match.end():])
    targets = [UNITS[wanted.group(1)]] if wanted and UNITS[wanted.group(1)] != source else [u for u in "cfk" if u != source]
    if source == "k" and value < 0:
        return "Kelvin can't be negative."
    celsius = _to_celsius(value, source)
    converted = " = ".join(f"{_fmt(_from_celsius(celsius, unit))} {SYMBOLS[unit]}" for unit in targets)
    return f"{_fmt(value)} {SYMBOLS[source]} = {converted}"
