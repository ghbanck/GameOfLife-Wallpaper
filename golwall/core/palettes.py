"""Colour schemes and the lookup tables the renderer paints with.

A cell is coloured by how long it has been alive: newborn cells flash in the
``birth`` colour, settle towards ``mature``, and old survivors (still lifes,
which would otherwise dominate a quiet board) sink into ``old`` so they recede
into the background.  Cells that just died leave a ``trail`` that fades out,
which is what makes the wallpaper read as motion rather than as blinking dots.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class Palette:
    """A colour scheme.  The background is black in all of them: anything else
    tints the whole screen and fights the grid, which is deliberately neutral."""

    name: str
    bg: RGB
    birth: RGB
    mature: RGB
    old: RGB
    trail: RGB


PALETTES: tuple[Palette, ...] = (
    Palette("matrix",    (0, 0, 0),    (190, 255, 205), (45, 220, 95),  (18, 105, 48),  (10, 60, 30)),
    Palette("ember",     (0, 0, 0),   (255, 240, 200), (255, 140, 30), (150, 50, 10),  (70, 20, 5)),
    Palette("ice",       (0, 0, 0),   (235, 250, 255), (80, 190, 255), (25, 80, 160),  (10, 35, 80)),
    Palette("mono",      (0, 0, 0), (255, 255, 255), (225, 225, 225),(120, 120, 120),(45, 45, 45)),
    Palette("synthwave", (0, 0, 0),  (255, 240, 255), (255, 60, 180), (120, 40, 200), (60, 20, 90)),
    Palette("bio",       (0, 0, 0),   (220, 255, 180), (120, 230, 80), (40, 120, 60),  (18, 55, 35)),
    Palette("solar",     (0, 0, 0),  (255, 255, 230), (255, 205, 60), (180, 90, 20),  (70, 40, 10)),
    Palette("deepsea",   (0, 0, 0),  (200, 255, 250), (0, 200, 190),  (10, 90, 120),  (5, 40, 60)),
)

BY_NAME = {p.name: p for p in PALETTES}


def get(name: str) -> Palette:
    try:
        return BY_NAME[name]
    except KeyError:
        raise ValueError(f"unknown palette {name!r}; have {', '.join(BY_NAME)}") from None


def names() -> tuple[str, ...]:
    return tuple(BY_NAME)


def _ramp(stops: list[tuple[float, RGB]], size: int = 256) -> np.ndarray:
    """Piecewise-linear gradient through (position, colour) stops -> (size, 3)."""
    xs = np.linspace(0.0, 1.0, size)
    pos = np.array([s[0] for s in stops])
    out = np.empty((size, 3), np.float64)
    for c in range(3):
        out[:, c] = np.interp(xs, pos, [s[1][c] for s in stops])
    return out


def build_luts(p: Palette, age_span: int = 20) -> tuple[np.ndarray, np.ndarray]:
    """Return (alive, trail) lookup tables as (256, 4) uint8 arrays in BGRA.

    ``alive`` is indexed by the cell age clamped to 255; ``trail`` by the decay
    value of a dead cell.  BGRA is the byte order of the B8G8R8A8 texture the
    renderer samples, so the table uploads with no shuffling.
    """
    knee = min(0.95, max(0.02, age_span / 255.0))
    alive = _ramp([(0.0, p.birth), (knee * 0.45, p.mature), (knee, p.mature), (1.0, p.old)])
    # Fade the trail with a slight curve so the tail lingers instead of
    # dropping off linearly.
    t = np.linspace(0.0, 1.0, 256)[:, None] ** 1.35
    trail = np.array(p.bg, np.float64) + t * (np.array(p.trail, np.float64) - np.array(p.bg))
    return _to_bgra(alive), _to_bgra(trail)


def _to_bgra(rgb: np.ndarray) -> np.ndarray:
    out = np.zeros((rgb.shape[0], 4), np.uint8)
    clipped = np.clip(rgb, 0, 255).astype(np.uint8)
    out[:, 0] = clipped[:, 2]   # B
    out[:, 1] = clipped[:, 1]   # G
    out[:, 2] = clipped[:, 0]   # R
    out[:, 3] = 255
    return out


def lerp_luts(a: tuple[np.ndarray, np.ndarray], b: tuple[np.ndarray, np.ndarray],
              t: float) -> tuple[np.ndarray, np.ndarray]:
    """Blend two LUT pairs, for crossfading between palettes."""
    t = min(1.0, max(0.0, t))
    mix = lambda x, y: (x.astype(np.float32) * (1 - t) + y.astype(np.float32) * t).astype(np.uint8)
    return mix(a[0], b[0]), mix(a[1], b[1])
