"""An animated arc-reactor widget (Tk canvas) - JARVIS's glowing "face".

Pure Tkinter: the glow is faked with stacked concentric ovals stepping through a colour gradient
(Tk has no alpha or blur), the coil ring rotates, and the core pulses. It reacts to state:
idle breathes slowly, busy spins up and brightens, speaking pulses hard, error goes red. Drawing
pauses while the panel is hidden so it costs no CPU in the background.
"""
from __future__ import annotations

import math
import tkinter as tk

_BG = (5, 7, 13)  # deep space, matches the panel background


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _hex(color) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in color)


def _mix(a, b, t: float):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


# core colour, glow colour, spin speed (deg/frame), pulse depth, phase speed
STATES = {
    "offline":  {"core": _rgb("#9fb3c8"), "glow": _rgb("#243447"), "spin": 0.15, "pulse": 0.12, "speed": 0.04},
    "idle":     {"core": _rgb("#eaffff"), "glow": _rgb("#1e90ff"), "spin": 0.5,  "pulse": 0.35, "speed": 0.06},
    "busy":     {"core": _rgb("#ffffff"), "glow": _rgb("#38bdf8"), "spin": 3.4,  "pulse": 0.55, "speed": 0.17},
    "speaking": {"core": _rgb("#eaffff"), "glow": _rgb("#3ef0ff"), "spin": 1.3,  "pulse": 0.95, "speed": 0.24},
    "error":    {"core": _rgb("#ffe0e0"), "glow": _rgb("#ff3b3b"), "spin": 0.8,  "pulse": 0.5,  "speed": 0.11},
}


class ArcReactor(tk.Canvas):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, highlightthickness=0, borderwidth=0, bg=_hex(_BG), **kwargs)
        self.state_name = "offline"
        self.phase = 0.0
        self.spin = 0.0
        self._visible = True
        self.bind("<Configure>", lambda _e: self._draw())
        self.after(60, self._tick)

    def set_state(self, name: str) -> None:
        if name in STATES:
            self.state_name = name

    def set_visible(self, visible: bool) -> None:
        self._visible = bool(visible)

    # ---- animation ----------------------------------------------------------------------------

    def _tick(self) -> None:
        style = STATES[self.state_name]
        self.phase += style["speed"]
        self.spin = (self.spin + style["spin"]) % 360
        showing = self._visible and self.winfo_viewable()
        if showing:
            try:
                self._draw()
            except tk.TclError:
                return  # widget destroyed
        self.after(66 if showing else 400, self._tick)

    def _oval(self, cx, cy, r, **kwargs) -> None:
        self.create_oval(cx - r, cy - r, cx + r, cy + r, **kwargs)

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        cx, cy, radius = w / 2, h / 2, min(w, h) / 2 - 8
        if radius < 24:
            return
        style = STATES[self.state_name]
        glow, core = style["glow"], style["core"]
        pulse = (math.sin(self.phase) * 0.5 + 0.5) * style["pulse"]  # 0 .. pulse

        # outer halo: dark at the rim, glowing toward the ring
        halo_steps = 18
        for i in range(halo_steps):
            t = i / halo_steps
            rad = radius * (1.4 - 0.55 * t)
            self._oval(cx, cy, rad, fill=_hex(_mix(_BG, glow, (t ** 2) * (0.45 + 0.55 * pulse))), outline="")

        # metallic housing rings
        self._oval(cx, cy, radius * 0.92, outline=_hex(_mix(glow, (255, 255, 255), 0.25)), width=2)
        self._oval(cx, cy, radius * 0.80, outline=_hex(_mix(_BG, glow, 0.55)), width=1)

        # rotating coil ring (the ten segments of the movie reactor)
        segments, seg_r = 10, radius * 0.60
        coil_col = _hex(_mix(glow, core, 0.3 + 0.35 * pulse))
        coil_w = max(2, radius * 0.055)
        for k in range(segments):
            self.create_arc(cx - seg_r, cy - seg_r, cx + seg_r, cy + seg_r,
                            start=(self.spin + k * (360 / segments) + 4) % 360, extent=26,
                            style="arc", outline=coil_col, width=coil_w)

        # inner glow building to the core
        core_steps = 14
        for i in range(core_steps):
            t = i / core_steps
            rad = radius * 0.44 * (1 - t) + 2
            self._oval(cx, cy, rad, fill=_hex(_mix(glow, core, t * (0.65 + 0.35 * pulse))), outline="")

        # bright pulsing core
        self._oval(cx, cy, radius * 0.14 * (1 + 0.28 * pulse), fill=_hex(core), outline="")

        # faint triangle motif, slowly counter-rotating
        tri_r = tri = radius * 0.34
        points = []
        for k in range(3):
            a = math.radians(-90 + k * 120 - self.spin * 0.35)
            points += [cx + tri * math.cos(a), cy + tri * math.sin(a)]
        self.create_polygon(points, outline=_hex(_mix(glow, core, 0.45)), fill="", width=1)
