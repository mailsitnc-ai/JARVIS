"""The reactor widget - JARVIS's glowing face.

It plays a baked loop of frames (ui/reactor_frames/): the orbital RINGS spin, the CORE stays fixed, and
the frames are rendered at high resolution so they stay crisp. Spin speed reacts to state (idle slow,
busy fast, speaking medium, offline crawl). Drawing pauses while the panel is hidden. If Pillow or the
frames are missing it falls back to a simple drawn glow so the panel never breaks.
"""
from __future__ import annotations

import math
import tkinter as tk
from pathlib import Path

try:
    from PIL import Image, ImageTk
    _PIL = True
except Exception:  # pragma: no cover
    _PIL = False

_BG = "#05070d"
_FRAMES_DIR = Path(__file__).with_name("reactor_frames")

# spin speed = frames advanced per tick (higher = faster). No per-frame work, just cycling cached frames.
STATES = {
    "offline":  {"step": 1},
    "idle":     {"step": 1},
    "busy":     {"step": 3},
    "speaking": {"step": 2},
    "error":    {"step": 2},
}


class ArcReactor(tk.Canvas):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, highlightthickness=0, borderwidth=0, bg=_BG, **kwargs)
        self.state_name = "offline"
        self._visible = True
        self._index = 0.0
        self._img_id = None
        self._phase = 0.0

        self._sources: list = []          # full-size PIL frames
        if _PIL and _FRAMES_DIR.is_dir():
            for path in sorted(_FRAMES_DIR.glob("frame_*.png")):
                try:
                    self._sources.append(Image.open(path).convert("RGB"))
                except Exception:
                    pass
        self._photos: list = []           # frames resized for the current widget size
        self._photo_size = None

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
                if self._sources:
                    self._show_frame()
                else:
                    self._draw_fallback()
            except tk.TclError:
                return
        self.after(66 if showing else 400, self._tick)
        if showing and self._photos:
            self._index = (self._index + STATES[self.state_name]["step"]) % len(self._photos)

    def _fit_size(self) -> int:
        w, h = self.winfo_width(), self.winfo_height()
        return max(0, min(min(w, h), 560))

    def _show_frame(self) -> None:
        size = self._fit_size()
        if size < 40:
            return
        if size != self._photo_size:
            self._photos = [ImageTk.PhotoImage(f.resize((size, size), Image.LANCZOS)) for f in self._sources]
            self._photo_size = size
            self._index = min(self._index, len(self._photos) - 1)
        cx, cy = self.winfo_width() / 2, self.winfo_height() / 2
        img = self._photos[int(self._index) % len(self._photos)]
        if self._img_id is None:
            self._img_id = self.create_image(cx, cy, image=img)
        else:
            self.itemconfig(self._img_id, image=img)
            self.coords(self._img_id, cx, cy)

    # ---- fallback (no Pillow / no frames) ------------------------------------------------------

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
                             fill=f"#{shade//4:02x}{shade//2:02x}{min(255, shade):02x}", outline="")
