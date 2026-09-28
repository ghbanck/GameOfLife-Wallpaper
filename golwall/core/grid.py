"""Where the grid lines fall, in the spirit of Blender's viewport floor.

Several nested levels exist at once -- every cell, every eighth, every
sixty-fourth -- and each fades in only once its lines are far enough apart to
read.  This module decides *where* the lines are and *how strong* they should
be; the renderer's pixel shader draws them, from the per-level opacities that
``opacity_for`` computes.

Zoomed out (a pixel covers a k x k block of cells) a line lands on every pixel
whose block holds a multiple of the level's step, and a level only counts once
its lines are at least two pixels apart -- by then the fade has long hidden it.
"""

from __future__ import annotations

from dataclasses import dataclass

RGB = tuple[int, int, int]

# The grid is always white, at one opacity, whatever palette is running: a
# grid tinted by the palette reads as part of the simulation rather than as a
# rule over it, and changes colour under you when the palette cycles.
WHITE = (255, 255, 255)
DEFAULT_OPACITY = 0.15

# A level is invisible below this spacing and fully faded in above it.
_FADE_FROM, _FADE_TO = 4.0, 12.0


def blend(bg: RGB, fg: RGB, t: float) -> RGB:
    t = max(0.0, min(1.0, t))
    return tuple(int(round(b + (f - b) * t)) for b, f in zip(bg, fg))


@dataclass(frozen=True)
class Line:
    """A one-pixel rule across the screen, to be blended at ``opacity``."""

    vertical: bool
    position: int
    colour: RGB
    opacity: float


class Grid:
    def __init__(self, levels=(1, 8, 64), strength: float = 1.0,
                 opacity: float = DEFAULT_OPACITY) -> None:
        self.levels = tuple(sorted({max(1, int(n)) for n in levels}))
        self.strength = float(strength)
        self.opacity = float(opacity)
        self.enabled = True

    def opacity_for(self, step: int, zoom: float) -> float:
        """How opaque a level is at ``zoom`` pixels per cell, or 0 when its lines are too close."""
        spacing = step * zoom
        fade = (spacing - _FADE_FROM) / (_FADE_TO - _FADE_FROM)
        if fade <= 0.0:
            return 0.0
        return max(0.0, min(1.0, self.opacity * min(1.0, fade) * self.strength))

    def lines(self, viewport, screen_w: int, screen_h: int):
        """Every visible line; coarser levels come last, so they land on top."""
        if not self.enabled or self.strength <= 0:
            return
        shrink = getattr(viewport, "shrink", 1)
        if shrink > 1:
            yield from self._shrunk_lines(viewport, shrink)
            return
        for step in self.levels:
            opacity = self.opacity_for(step, viewport.zoom)
            if opacity <= 0.0:
                continue
            spacing = step * viewport.zoom
            offset = (-viewport.cell_x) % step
            x = viewport.offset_x + offset * viewport.zoom
            while x < screen_w:
                if x >= 0:
                    yield Line(True, x, WHITE, opacity)
                x += spacing
            offset = (-viewport.cell_y) % step
            y = viewport.offset_y + offset * viewport.zoom
            while y < screen_h:
                if y >= 0:
                    yield Line(False, y, WHITE, opacity)
                y += spacing

    def _shrunk_lines(self, viewport, shrink: int):
        """Zoomed out: a line on each pixel whose block of cells holds a multiple of the step."""
        for step in self.levels:
            opacity = self.opacity_for(step, 1 / shrink)
            if opacity <= 0.0 or step < 2 * shrink:
                continue
            for vertical, first, span in ((True, viewport.cell_x, viewport.cols),
                                          (False, viewport.cell_y, viewport.rows)):
                cell = (-first) % step          # cells from the screen edge to the first multiple
                while cell < span:
                    yield Line(vertical, cell // shrink, WHITE, opacity)
                    cell += step
