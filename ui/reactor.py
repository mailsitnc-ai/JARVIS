"""The reactor widget - JARVIS's glowing face.

It displays the reactor artwork (ui/reactor.png, the reference recoloured blue) and brings it to life:
a slow 3D-looking spin and a breathing pulse, precomputed into a short loop of frames and cached, so
runtime is just blitting. It reacts to state - idle breathes, busy spins up and brightens, speaking
pulses hard, error tints warm, offline dims - and pauses while the panel is hidden. If Pillow or the
image is unavailable it falls back to a simple drawn glow so the panel never breaks.
"""
from __future__ import annotations

import math
import tkinter as tk
from pathlib import Path

try:
    from PIL import Image, ImageEnhance, ImageTk
    _PIL = True
except Exception:  # pragma: no cover - Pillow should be installed
    _PIL = False

_BG = "#05070d"
_ASSET = Path(__file__).with_name("reactor.png")
_FRAMES = 36

# brightness base, pulse amount, spin step (frames advanced per tick -> spin speed), warm(error) flag
STATES = {
    "offline":  {"bright": 0.40, "pulse": 0.05, "step": 1, "warm": False},
    "idle":     {"bright": 0.92, "pulse": 0.12, "step": 1, "warm": False},
    "busy":     {"bright": 1.15, "pulse": 0.18, "step": 3, "warm": False},
    "speaking": {"bright": 1.10, "pulse": 0.30, "step": 2, "warm": False},
    "error":    {"bright": 1.05, "pulse": 0.22, "step": 2, "warm": True},
}


class ArcReactor(tk.Canvas):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, highlightthickness=0, borderwidth=0, bg=_BG, **kwargs)
        self.state_name = "offline"
        self._visible = True
        self._index = 0
        self._frames: list = []
        self._built_key = None            # (state, size) the current frames were built for
        self._img_id = None
        self._phase = 0.0

        self._blue = self._orange = None
        if _PIL and _ASSET.is_file():
            try:
                self._blue = Image.open(_ASSET).convert("RGB")
                r, g, b = self._blue.split()
                self._orange = Image.merge("RGB", (b, g, r))  # for the error state
            except Exception:
                self._blue = None

        self.bind("<Configure>", lambda _e: None)  # size is picked up by the tick
        self.after(60, self._tick)

    def set_state(self, name: str) -> None:
        if name in STATES:
            self.state_name = name

    def set_visible(self, visible: bool) -> None:
        self._visible = bool(visible)

    # ---- animation ----------------------------------------------------------------------------

    def _tick(self) -> None:
        showing = self._visible and self.winfo_viewable()
        if showing:
            try:
                if self._blue is not None:
                    self._render_image_frame()
                else:
                    self._draw_fallback()
            except tk.TclError:
                return
        step = STATES[self.state_name]["step"]
        self.after(66 if showing else 400, self._tick)
        if showing and self._frames:
            self._index = (self._index + step) % len(self._frames)

    def _size(self) -> int:
        w, h = self.winfo_width(), self.winfo_height()
        return max(0, min(min(w, h), 560))  # square, capped so frame-building stays cheap

    def _render_image_frame(self) -> None:
        size = self._size()
        if size < 40:
            return
        key = (self.state_name, size)
        if key != self._built_key:
            self._build_frames(size)
            self._built_key = key
            self._index = min(self._index, len(self._frames) - 1)
        if not self._frames:
            return
        cx, cy = self.winfo_width() / 2, self.winfo_height() / 2
        img = self._frames[self._index]
        if self._img_id is None:
            self._img_id = self.create_image(cx, cy, image=img)
        else:
            self.itemconfig(self._img_id, image=img)
            self.coords(self._img_id, cx, cy)

    def _build_frames(self, size: int) -> None:
        style = STATES[self.state_name]
        src = (self._orange if style["warm"] and self._orange is not None else self._blue)
        base = src.resize((size, size), Image.LANCZOS)
        frames = []
        for k in range(_FRAMES):
            ang = 360.0 * k / _FRAMES
            pulse = style["bright"] + style["pulse"] * math.sin(4 * math.pi * k / _FRAMES)
            frame = base.rotate(ang, resample=Image.BILINEAR, expand=False)
            frame = ImageEnhance.Brightness(frame).enhance(pulse)
            frames.append(ImageTk.PhotoImage(frame))
        self._frames = frames

    # ---- fallback (no Pillow / no asset): a simple drawn glow ----------------------------------

    def _draw_fallback(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        cx, cy, radius = w / 2, h / 2, min(w, h) / 2 - 8
        if radius < 20:
            return
        self._phase += 0.08
        pulse = math.sin(self._phase) * 0.5 + 0.5
        for i in range(16):
            t = i / 16
            rad = radius * (0.9 - 0.8 * t)
            shade = int(30 + 200 * t * (0.6 + 0.4 * pulse))
            self.create_oval(cx - rad, cy - rad, cx + rad, cy + rad,
                             fill=f"#{shade//4:02x}{shade//2:02x}{min(255,shade):02x}", outline="")
