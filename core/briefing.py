"""JARVIS's briefing: "Good morning, sir. It's 7:42 on Wednesday the 23rd..." - time, weather, email,
battery and what's on JARVIS's own agenda, in a couple of spoken-length sentences.

Weather is from Open-Meteo (free, no key). The location is config `briefing.city` if set; otherwise
it is looked up once from your internet connection (ipapi.co) and cached in the config, so the lookup
happens only the first time. Every part is best-effort: a part that fails is simply left out.
"""
from __future__ import annotations

import datetime as _dt
import json
import urllib.parse
import urllib.request

_UA = {"User-Agent": "JARVIS/1.0"}

# WMO weather codes -> words
_WMO = {0: "clear skies", 1: "mostly clear skies", 2: "some cloud", 3: "overcast skies", 45: "fog", 48: "fog",
        51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain",
        65: "heavy rain", 66: "freezing rain", 67: "freezing rain", 71: "light snow", 73: "snow",
        75: "heavy snow", 80: "passing showers", 81: "showers", 82: "heavy showers", 95: "thunderstorms",
        96: "thunderstorms with hail", 99: "thunderstorms with hail"}


def _get_json(url: str, timeout: float = 6.0):
    with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def greeting(now: _dt.datetime) -> str:
    h = now.hour
    return "Good morning" if 4 <= h < 12 else ("Good afternoon" if h < 17 else "Good evening")


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def time_line(now: _dt.datetime) -> str:
    clock = now.strftime("%I:%M %p").lstrip("0")
    return f"It's {clock} on {now.strftime('%A')} the {_ordinal(now.day)}."


def location(settings) -> tuple[str, float, float] | None:
    """(city, lat, lon): configured city -> geocoded; else a one-time IP lookup, cached in config."""
    city = str(settings.get("briefing.city", "") or "").strip()
    cached = settings.get("briefing.coords")
    if isinstance(cached, dict) and cached.get("lat") is not None and (not city or cached.get("city") == city):
        return cached.get("city", city), float(cached["lat"]), float(cached["lon"])
    try:
        if city:
            res = _get_json("https://geocoding-api.open-meteo.com/v1/search?count=1&name="
                            + urllib.parse.quote(city)).get("results") or []
            if not res:
                return None
            loc = (res[0].get("name", city), float(res[0]["latitude"]), float(res[0]["longitude"]))
        else:
            ip = _get_json("https://ipapi.co/json/")
            loc = (ip.get("city") or "your area", float(ip["latitude"]), float(ip["longitude"]))
    except Exception:
        return None
    try:
        from core.config import set_user_value
        set_user_value("briefing.coords", {"city": loc[0], "lat": loc[1], "lon": loc[2]})
    except Exception:
        pass
    return loc


def weather_line(settings) -> str | None:
    loc = location(settings)
    if not loc:
        return None
    city, lat, lon = loc
    try:
        w = _get_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
            "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 1,
            "current": "temperature_2m,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max"}))
        cur, day = w["current"], w["daily"]
        temp = round(cur["temperature_2m"])
        sky = _WMO.get(int(cur.get("weather_code", 0)), "mixed conditions")
        hi, lo = round(day["temperature_2m_max"][0]), round(day["temperature_2m_min"][0])
        rain = day.get("precipitation_probability_max", [None])[0]
        line = f"In {city} it's {temp} degrees with {sky}; today ranges from {lo} to {hi}."
        if rain is not None and rain >= 40:
            line += f" There's a {rain} percent chance of rain - an umbrella wouldn't go amiss."
        return line
    except Exception:
        return None


def email_line() -> str | None:
    try:
        from core.google import GoogleAuth, GoogleClient
        auth = GoogleAuth()
        if not auth.is_connected():
            return None
        g = GoogleClient(auth)
        # Only what matters: unread, Primary tab (no promotions/social), from the last day.
        q = "is:unread in:inbox category:primary newer_than:1d"
        listing = g._get("https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=25&q="
                         + urllib.parse.quote(q))
        recent = listing.get("messages", [])
        if not recent:
            return "Nothing new in your inbox since yesterday."
        count = len(recent) if len(recent) < 25 else int(listing.get("resultSizeEstimate", 25))
        senders = []
        for m in recent[:6]:
            d = g._get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{m['id']}"
                       "?format=metadata&metadataHeaders=From")
            frm = next((h["value"] for h in d.get("payload", {}).get("headers", []) if h["name"] == "From"), "")
            name = frm.split("<")[0].strip().strip('"') or frm
            if name and name not in senders:
                senders.append(name)
            if len(senders) == 3:
                break
        plural = "email" if count == 1 else "emails"
        line = f"You have {count} new {plural} since yesterday"
        return line + (f", from {', '.join(senders)}." if senders else ".")
    except Exception:
        return None


def battery_line() -> str | None:
    from core.oslayer import battery
    b = battery()
    if not b:
        return None
    pct, plugged = b
    if plugged:
        return f"Battery's at {pct} percent and charging." if pct < 100 else None
    if pct <= 20:
        return f"Battery's at {pct} percent - I'd plug in soon, sir."
    return f"Battery's at {pct} percent."


def agenda_line(now: _dt.datetime) -> str | None:
    try:
        from core.agenda import AgendaStore, from_iso
        today = [t for t in AgendaStore().list() if t.get("enabled", True) and t.get("next_run")
                 and from_iso(t["next_run"]).date() == now.date()]
        if not today:
            return None
        titles = ", ".join(t.get("title", "a task") for t in today[:3])
        return f"On my own list for today: {titles}."
    except Exception:
        return None


def compose(settings, now: _dt.datetime | None = None) -> str:
    now = now or _dt.datetime.now()
    parts = [f"{greeting(now)}, sir.", time_line(now)]
    for fn in (lambda: weather_line(settings), email_line, battery_line, lambda: agenda_line(now)):
        try:
            line = fn()
        except Exception:
            line = None
        if line:
            parts.append(line)
    return " ".join(parts)
