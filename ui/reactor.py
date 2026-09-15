"""An animated energy-sphere reactor widget (Tk canvas) - JARVIS's glowing "face".

Pure Tkinter (no alpha/blur): a bright multi-layer core, a spray of radiating plasma rays, and a
rotating wireframe sphere of glowing lines whose front arcs are brighter than the back. It reacts to
state: idle breathes, busy spins up and brightens, speaking pulses hard, error goes red. Drawing pauses
while the panel is hidden so it costs no CPU in the background. The whole palette is electric blue.
"""
from __future__ import annotations

import math
import random
import tkinter as tk

_BG = (5, 7, 13)  # deep space, matches the panel background


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _hex(color) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in color)


def _mix(a, b, t: float):
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


# core colour, glow colour, ray colour, spin speed (deg/frame), pulse depth, phase speed
STATES = {
    "offline":  {"core": _rgb("#cfe0f0"), "glow": _rgb("#1b3a5c"), "ray": _rgb("#2d5f8f"), "spin": 0.12, "pulse": 0.10, "speed": 0.04},
    "idle":     {"core": _rgb("#eaffff"), "glow": _rgb("#1e90ff"), "ray": _rgb("#38bdf8"), "spin": 0.45, "pulse": 0.35, "speed": 0.06},
    "busy":     {"core": _rgb("#ffffff"), "glow": _rgb("#33aaff"), "ray": _rgb("#7fdbff"), "spin": 2.6,  "pulse": 0.55, "speed": 0.16},
    "speaking": {"core": _rgb("#f0ffff"), "glow": _rgb("#3ef0ff"), "ray": _rgb("#9becff"), "spin": 1.2,  "pulse": 0.95, "speed": 0.24},
    "error":    {"core": _rgb("#ffe0e0"), "glow": _rgb("#ff3b3b"), "ray": _rgb("#ff7a7a"), "spin": 0.8,  "pulse": 0.5,  "speed": 0.11},
}


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(v):
    m = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
    return (v[0] / m, v[1] / m, v[2] / m)


class ArcReactor(tk.Canvas):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, highlightthickness=0, borderwidth=0, bg=_hex(_BG), **kwargs)
        self.state_name = "offline"
        self.phase = 0.0
        self.spin = 0.0
        self._visible = True

        rng = random.Random(7)  # fixed seed: the field looks organic but is stable across redraws
        # radiating plasma rays: (angle deg, inner radius frac, outer frac, twinkle phase, width)
        self._rays = [(rng.uniform(0, 360), rng.uniform(0.10, 0.26), rng.uniform(0.55, 1.18),
                       rng.uniform(0, 6.28), rng.choice([1, 1, 1, 2])) for _ in range(110)]
        # sparks floating in the field: (angle, radius frac, twinkle phase)
        self._sparks = [(rng.uniform(0, 360), rng.uniform(0.5, 1.15), rng.uniform(0, 6.28)) for _ in range(46)]
        # great circles of the wireframe sphere, as orthonormal basis pairs (u, v) spanning each plane
        self._circles = []
        for n in [(0, 1, 0), (1, 0, 0), (0, 0, 1), (1, 1, 0), (1, 0, 1.2), (0.6, 1, 0.6)]:
            n = _norm(n)
            aux = (0, 0, 1) if abs(n[2]) < 0.9 else (0, 1, 0)
            u = _norm(_cross(n, aux))
            v = _cross(n, u)
            self._circles.append((u, v))

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

    @staticmethod
    def _rotate(p, yaw, tilt):
        x, y, z = p
        ca, sa = math.cos(yaw), math.sin(yaw)          # spin about the vertical axis
        x, z = x * ca + z * sa, -x * sa + z * ca
        cb, sb = math.cos(tilt), math.sin(tilt)        # fixed lean, so it reads as 3D
        y, z = y * cb - z * sb, y * sb + z * cb
        return x, y, z

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        cx, cy, radius = w / 2, h / 2, min(w, h) / 2 - 8
        if radius < 24:
            return
        style = STATES[self.state_name]
        glow, core, ray = style["glow"], style["core"], style["ray"]
        pulse = (math.sin(self.phase) * 0.5 + 0.5) * style["pulse"]  # 0 .. pulse
        yaw = math.radians(self.spin)
        tilt = math.radians(22)

        # 1. deep background halo, brightest toward the centre
        for i in range(16):
            t = i / 16
            self._oval(cx, cy, radius * (1.5 - 0.6 * t),
                       fill=_hex(_mix(_BG, glow, (t ** 2.2) * (0.30 + 0.5 * pulse))), outline="")

        # 2. radiating plasma rays (the particle spray)
        base_spin = self.spin * 0.25
        for ang, inner, outer, tw, wdt in self._rays:
            a = math.radians(ang + base_spin)
            twinkle = 0.35 + 0.65 * abs(math.sin(self.phase * 1.3 + tw))
            out = outer * (1 + 0.10 * pulse)
            x0, y0 = cx + math.cos(a) * radius * inner, cy + math.sin(a) * radius * inner
            x1, y1 = cx + math.cos(a) * radius * out, cy + math.sin(a) * radius * out
            col = _mix(_mix(ray, core, 0.25), glow, 0.35)
            self.create_line(x0, y0, x1, y1, fill=_hex(_mix(_BG, col, 0.25 + 0.75 * twinkle)), width=wdt)

        # 3. rotating wireframe sphere: great circles, front arcs brighter than the back
        rs = radius * 0.92
        seg = 30
        for u, v in self._circles:
            pts = []
            for k in range(seg + 1):
                t = 2 * math.pi * k / seg
                p = (u[0] * math.cos(t) + v[0] * math.sin(t),
                     u[1] * math.cos(t) + v[1] * math.sin(t),
                     u[2] * math.cos(t) + v[2] * math.sin(t))
                pts.append(self._rotate(p, yaw, tilt))
            for k in range(seg):
                (x0, y0, z0), (x1, y1, z1) = pts[k], pts[k + 1]
                front = ((z0 + z1) * 0.5 + 1) * 0.5  # 0 (back) .. 1 (front)
                bright = 0.12 + 0.85 * (front ** 1.5) * (0.7 + 0.5 * pulse)
                self.create_line(cx + x0 * rs, cy - y0 * rs, cx + x1 * rs, cy - y1 * rs,
                                 fill=_hex(_mix(_BG, _mix(glow, ray, front), bright)),
                                 width=2 if front > 0.72 else 1)

        # 4. a bright equatorial halo ring accent
        self._oval(cx, cy, rs, outline=_hex(_mix(glow, core, 0.2 + 0.3 * pulse)), width=1)
        self._oval(cx, cy, radius * 0.5, outline=_hex(_mix(_BG, ray, 0.5)), width=1)

        # 5. sparks in the field
        for ang, rf, tw in self._sparks:
            a = math.radians(ang - base_spin)
            tw_b = abs(math.sin(self.phase * 1.7 + tw))
            if tw_b < 0.4:
                continue
            x, y = cx + math.cos(a) * radius * rf, cy + math.sin(a) * radius * rf
            s = 1 + 1.4 * tw_b
            self._oval(x, y, s, fill=_hex(_mix(glow, core, tw_b)), outline="")

        # 6. inner glow building to the core
        for i in range(16):
            t = i / 16
            self._oval(cx, cy, radius * 0.40 * (1 - t) + 2, fill=_hex(_mix(glow, core, t * (0.6 + 0.4 * pulse))),
                       outline="")

        # 7. bright pulsing core with a white-hot centre
        self._oval(cx, cy, radius * 0.17 * (1 + 0.26 * pulse), fill=_hex(_mix(glow, core, 0.85)), outline="")
        self._oval(cx, cy, radius * 0.09 * (1 + 0.30 * pulse), fill=_hex(core), outline="")
