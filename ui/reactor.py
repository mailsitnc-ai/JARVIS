"""The reactor widget - JARVIS's glowing face, drawn entirely in code (no image assets).

At startup a background thread renders a loop of frames with ui/reactor_render.py: a STATIC mesh sphere
and white-hot core, and bold RINGS that actually orbit in 3D. The frames live in memory (never saved as
images); the widget resizes them to fit and cycles them. Spin speed reacts to state. Until the frames are
ready (or if Pillow/numpy are missing) it shows a simple drawn glow so the panel never blocks or breaks.
"""
from __future__ import annotations

import math
import threading
import tkinter as tk

try:
    from PIL import ImageTk
    from . import reactor_render
    _OK = True
except Exception:  # pragma: no cover
    _OK = False

_BG = "#05070d"
_BASE = 560          # frames are rendered once at this size, then resized to fit
_NFRAMES = 24

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

        self._pil_frames: list = []       # code-rendered frames (PIL), filled by the worker thread
        self._ready = False
        self._photos: list = []           # resized-to-fit PhotoImages for the current size
        self._photo_size = None

        if _OK:
            threading.Thread(target=self._render_all, name="reactor-render", daemon=True).start()
        self.after(60, self._tick)

    def set_state(self, name: str) -> None:
        if name in STATES:
            self.state_name = name

    def set_visible(self, visible: bool) -> None:
        self._visible = bool(visible)

    # ---- code rendering (worker thread) -------------------------------------------------------

    def _render_all(self) -> None:
        frames = []
        for k in range(_NFRAMES):
            try:
                frames.append(reactor_render.render(_BASE, 360.0 * k / _NFRAMES))
            except Exception:
                return
        self._pil_frames = frames
        self._ready = True

    # ---- animation ----------------------------------------------------------------------------

    def _tick(self) -> None:
        showing = self._visible and self.winfo_viewable()
        if showing:
            try:
                if self._ready:
                    self._show_frame()
                else:
                    self._draw_fallback()
            except tk.TclError:
                return
        # 15 fps while working/speaking, 10 fps idle (the idle spin is slow anyway), paused when hidden
        self.after((66 if self.state_name in ("busy", "speaking") else 100) if showing else 400, self._tick)
        if showing and self._photos:
            self._index = (self._index + STATES[self.state_name]["step"]) % len(self._photos)

    def _fit_size(self) -> int:
        w, h = self.winfo_width(), self.winfo_height()
        return max(0, min(min(w, h), 620))

    def _show_frame(self) -> None:
        size = self._fit_size()
        if size < 40:
            return
        if size != self._photo_size:
            self._photos = [ImageTk.PhotoImage(f.resize((size, size))) for f in self._pil_frames]
            self._photo_size = size
        if not self._photos:
            return
        cx, cy = self.winfo_width() / 2, self.winfo_height() / 2
        img = self._photos[int(self._index) % len(self._photos)]
        if self._img_id is None:
            self._img_id = self.create_image(cx, cy, image=img)
        else:
            self.itemconfig(self._img_id, image=img)
            self.coords(self._img_id, cx, cy)

    # ---- fallback (rendering not ready / no libs) ----------------------------------------------

    def _draw_fallback(self) -> None:
        self.delete("all")
        self._img_id = None
        w, h = self.winfo_width(), self.winfo_height()
        cx, cy, radius = w / 2, h / 2, min(w, h) / 2 - 8
        if radius < 20:
            return
        self._phase += 0.08
        pulse = math.sin(self._phase) * 0.5 + 0.5
        for i in range(16):
            t = i / 16
            rad = radius * (0.5 - 0.45 * t)
            shade = int(40 + 200 * t * (0.6 + 0.4 * pulse))
            self.create_oval(cx - rad, cy - rad, cx + rad, cy + rad,
                             fill=f"#{shade//5:02x}{shade//2:02x}{min(255, shade):02x}", outline="")
