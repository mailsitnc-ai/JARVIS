"""Procedural reactor renderer - the reactor is DRAWN in code, no image assets.

A frame is: a STATIC tangled mesh sphere (chords between points on a sphere) + a STATIC white-hot core
with radiating spikes + bold RINGS that actually orbit in 3D (real ellipse motion, not a rotated bitmap)
+ sparks, composited with real additive glow and tone-mapped to a blue palette. `render(size, deg)` gives
the frame at ring-orbit angle `deg`; the panel builds a loop of these once (in a thread) and cycles them.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageFilter

_rs = np.random.RandomState(17)


def _unit(n):
    d = _rs.randn(n, 3)
    return d / np.linalg.norm(d, axis=1, keepdims=True)


# ---- fixed geometry, generated once (deterministic) ----
_MESH = _unit(150)                                   # points on the sphere for the tangled mesh
_CHORDS = []                                          # (i, j) pairs -> straight lines across the sphere
for _i in range(len(_MESH)):
    order = np.argsort(((_MESH - _MESH[_i]) ** 2).sum(1))
    for _j in order[1:6]:                             # connect to 5 nearest -> dense crisscross
        if _i < _j:
            _CHORDS.append((_i, int(_j)))
_CHORDS = np.array(_CHORDS)

_RING_BASES = []                                      # (u, v, width, bright) for each bold orbiting ring
for _k, _n in enumerate([(0.15, 1, 0.1), (0.9, 0.25, 0.2), (0.2, 0.6, 1.0)]):
    _n = np.array(_n, float); _n /= np.linalg.norm(_n)
    _aux = np.array([0, 0, 1.0]) if abs(_n[2]) < 0.9 else np.array([0, 1.0, 0])
    _u = np.cross(_n, _aux); _u /= np.linalg.norm(_u); _v = np.cross(_n, _u)
    _RING_BASES.append((_u.astype(np.float32), _v.astype(np.float32), 6 + 4 * _k, 1.0))

_SPARK = (_unit(500) * (0.9 + 0.5 * _rs.rand(500, 1))).astype(np.float32)   # debris orbits with the rings
_RAY_ANG = _rs.rand(120) * 2 * math.pi
_RAY_LEN = 0.2 + 0.5 * _rs.rand(120)
_RAY_B = 0.4 + 0.8 * _rs.rand(120)
_TILT = math.radians(15)


def _viewmat(yaw):
    Ry = np.array([[math.cos(yaw), 0, math.sin(yaw)], [0, 1, 0], [-math.sin(yaw), 0, math.cos(yaw)]])
    Rx = np.array([[1, 0, 0], [0, math.cos(_TILT), -math.sin(_TILT)], [0, math.sin(_TILT), math.cos(_TILT)]])
    return (Rx @ Ry).astype(np.float32)


_VIEW = _viewmat(0.0)                                 # the sphere itself never spins


def render(size: int, deg: float) -> Image.Image:
    SS = int(size * 1.3)                              # supersample then downscale = crisp
    c = SS / 2
    R = SS / 2 - 6
    Rs = 0.9 * R
    acc = np.zeros((SS, SS), np.float32)

    def line(x0, y0, x1, y1, b0, b1, w=1):
        n = max(2, int(math.hypot(x1 - x0, y1 - y0)))
        xs = np.linspace(x0, x1, n); ys = np.linspace(y0, y1, n); bs = np.linspace(b0, b1, n)
        for dx in range(w):                           # cheap thickness: a few offset passes
            off = dx - (w - 1) / 2
            ix = np.clip(xs + off, 0, SS - 1).astype(np.int32); iy = np.clip(ys, 0, SS - 1).astype(np.int32)
            np.add.at(acc, (iy, ix), bs)
            iy2 = np.clip(ys + off, 0, SS - 1).astype(np.int32); ix2 = np.clip(xs, 0, SS - 1).astype(np.int32)
            np.add.at(acc, (iy2, ix2), bs)

    # STATIC tangled mesh sphere
    proj = _MESH @ _VIEW.T
    sx = c + proj[:, 0] * Rs; sy = c - proj[:, 1] * Rs; sz = proj[:, 2]
    for i, j in _CHORDS:
        f = (sz[i] + sz[j]) * 0.25 + 0.5
        b = 0.18 + 0.5 * f
        line(sx[i], sy[i], sx[j], sy[j], b, b)

    # bold RINGS that actually orbit in 3D
    yaw = math.radians(deg)
    RM = _viewmat(yaw)
    for u, v, w, bright in _RING_BASES:
        t = np.linspace(0, 2 * math.pi, 400)
        p = (np.cos(t)[:, None] * u + np.sin(t)[:, None] * v) @ RM.T
        px = c + p[:, 0] * Rs; py = c - p[:, 1] * Rs; pz = p[:, 2]
        for k in range(len(t) - 1):
            f = (pz[k] + 1) * 0.5
            b = bright * (0.2 + 0.9 * f ** 1.4)
            line(px[k], py[k], px[k + 1], py[k + 1], b, b, w=int(w * (0.5 + f)))

    # debris sparks (orbit with the rings)
    p = _SPARK @ RM.T
    f = np.clip((p[:, 2] + 1) * 0.5, 0, 1)
    ix = np.clip(c + p[:, 0] * Rs, 0, SS - 1).astype(np.int32); iy = np.clip(c - p[:, 1] * Rs, 0, SS - 1).astype(np.int32)
    np.add.at(acc, (iy, ix), 1.2 * (0.3 + 0.9 * f))

    # STATIC core rays
    for a, ln, b in zip(_RAY_ANG, _RAY_LEN, _RAY_B):
        line(c, c, c + math.cos(a) * ln * R, c + math.sin(a) * ln * R, b, b * 0.05)

    # glow (blur through a fixed-scale 8-bit copy) + static core bloom
    K = 6.0
    si = Image.fromarray(np.clip(acc / K * 255, 0, 255).astype(np.uint8), "L")
    glow = (0.6 * np.asarray(si.filter(ImageFilter.GaussianBlur(3)), np.float32)
            + 0.4 * np.asarray(si.filter(ImageFilter.GaussianBlur(11)), np.float32)) / 255 * K
    yy, xx = np.mgrid[0:SS, 0:SS]
    d2 = (xx - c) ** 2 + (yy - c) ** 2
    core = (2.4 * np.exp(-d2 / (2 * (0.11 * R) ** 2)) + 4.0 * np.exp(-d2 / (2 * (0.055 * R) ** 2))
            + 7.0 * np.exp(-d2 / (2 * (0.025 * R) ** 2)))
    field = acc * 1.25 + glow + core
    t = np.clip(1 - np.exp(-field * 0.5), 0, 1)

    stops = [(0.00, (2, 6, 18)), (0.12, (14, 50, 140)), (0.32, (26, 126, 255)),
             (0.60, (95, 205, 255)), (0.82, (205, 244, 255)), (1.00, (255, 255, 255))]
    r = np.zeros_like(t); g = np.zeros_like(t); bl = np.zeros_like(t)
    for (t0, c0), (t1, c1) in zip(stops[:-1], stops[1:]):
        m = (t >= t0) & (t <= t1); ff = (t[m] - t0) / (t1 - t0 + 1e-9)
        r[m] = c0[0] + (c1[0] - c0[0]) * ff; g[m] = c0[1] + (c1[1] - c0[1]) * ff; bl[m] = c0[2] + (c1[2] - c0[2]) * ff
    img = Image.fromarray(np.stack([r, g, bl], -1).astype(np.uint8), "RGB").resize((size, size), Image.LANCZOS)
    c2 = size / 2; yy2, xx2 = np.mgrid[0:size, 0:size]
    vig = np.clip((0.5 * size - np.sqrt((xx2 - c2) ** 2 + (yy2 - c2) ** 2)) / (0.06 * size), 0, 1)[..., None]
    return Image.fromarray((np.asarray(img, np.float32) * vig).astype(np.uint8), "RGB")


if __name__ == "__main__":
    render(560, 0).save("preview.png")
    render(560, 90).save("preview9.png")
    print("preview.png preview9.png")
