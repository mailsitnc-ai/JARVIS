"""Describe an image with a vision-capable model (Gemini's multimodal endpoint).

Used by the camera and screen-observation skills. Returns a text description, or None if no vision model
is available (no Gemini key). Self-contained: reads the Gemini config + key and posts the image inline.
"""
from __future__ import annotations

import base64
from pathlib import Path

from . import keystore
from .config import load_settings
from .llm_router import http_json


def available() -> bool:
    return bool(keystore.get_key("gemini"))


def describe_image(image_path: str, question: str = "Describe what you see in this image, concisely.") -> str | None:
    key = keystore.get_key("gemini")
    if not key:
        return None
    path = Path(image_path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return f"(couldn't read the image: {exc})"
    settings = load_settings()
    cfg = settings.get("llm.gemini", {}) or {}
    base = str(cfg.get("base_url", "https://generativelanguage.googleapis.com/v1beta/openai")).rstrip("/")
    model = str(cfg.get("model", "gemini-2.5-flash"))
    ext = path.suffix.lstrip(".").lower() or "png"
    data_uri = f"data:image/{ 'jpeg' if ext in ('jpg', 'jpeg') else ext };base64," + base64.b64encode(raw).decode()
    body = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]}],
        "max_tokens": 400,
        "temperature": 0.2,
    }
    response = http_json("POST", base + "/chat/completions", body,
                         {"Authorization": f"Bearer {key}"}, timeout=60)
    if response.status != 200 or not response.body:
        return f"(vision request failed: HTTP {response.status})"
    try:
        return response.body["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        return "(vision model returned no description)"
