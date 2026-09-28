"""The window onto the world: crisp zoom levels, smooth wrapping pan.

Zoom is screen pixels per cell.  Zoomed in it is a whole number, so cells stay
crisp squares at every level.  Zoomed out it is 1/k: each screen pixel covers
a k x k block of cells, which the renderer reduces to one colour, so a world
several screens big can still be seen whole.

"Fit" is the closest zoom that shows the world without repeating it.  The
wheel can go further out than that: the world is a torus, so what lies past
its edge is the world again, and the view shows it repeating -- up to a few
copies across, and never finer than 1/4, below which a frame gets too slow
to draw.

Panning is smooth because the camera's position is fractional.  Zoomed in, the
whole-cell part selects which cell sits at the top-left of the screen and the
fraction becomes a sub-cell pixel offset.  Zoomed out, the top-left snaps down
to a multiple of k instead: the blocks then stay fixed in the world and a pan
moves whole pixels, where re-cutting every block on each move would shimmer.
The renderer samples the world directly, wrapping around the torus, so no zoom
needs a buffer the size of the view.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Cells per screen pixel at the zoomed-out levels.  They divide the usual sizes
# evenly: a 7680-cell world is 1/2 on a 3840 screen and 1/4 on a 1920 one.
SHRINKS = (2, 3, 4, 6, 8, 12, 16)
# Zoomed out past the fit, at most this many copies of the world across the
# screen, and no finer than 1/FREE_SHRINK: at 1/8 a pixel reduces 64 cells and
# a 4K frame costs ~45 ms of GPU time, at 1/16 ~160 ms.
MAX_REPEATS = 8
FREE_SHRINK = 4


@dataclass
class Viewport:
    """What the renderer needs to draw one frame."""

    cell_x: int      # world column at the top-left of the screen
    cell_y: int      # world row at the top-left of the screen
    cols: int        # cells spanned across the screen (never more than the world)
    rows: int
    offset_x: int    # pixel position of that first cell's left edge (<= 0)
    offset_y: int
    zoom: int        # screen pixels per cell; 1 while zoomed out
    shrink: int = 1  # cells per screen pixel; > 1 only while zoomed out

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return self.cell_x, self.cell_y, self.cols, self.rows

    @property
    def scale(self) -> float:
        """Screen pixels per cell, fractional when zoomed out -- what the grid fades by."""
        return self.zoom if self.shrink <= 1 else 1 / self.shrink


class Camera:
    MAX_ZOOM = 64
    # Every zoom the camera can rest at, smallest first; whole levels stay ints.
    LEVELS: tuple[float, ...] = (tuple(1 / k for k in reversed(SHRINKS))
                                 + tuple(range(1, MAX_ZOOM + 1)))
    MIN_ZOOM = LEVELS[0]

    def __init__(self, screen_w: int, screen_h: int, world_w: int, world_h: int,
                 zoom: float = 8) -> None:
        self.screen_w, self.screen_h = max(1, int(screen_w)), max(1, int(screen_h))
        self.world_w, self.world_h = int(world_w), int(world_h)
        self.zoom = self.clamp_zoom(zoom)
        self.version = 0
        self.center()

    # -- limits -----------------------------------------------------------
    def min_zoom(self) -> float:
        """How far out the camera may go: past the fit, the torus is seen repeating."""
        bound = max(self.screen_w / (MAX_REPEATS * max(1, self.world_w)),
                    self.screen_h / (MAX_REPEATS * max(1, self.world_h)), 1 / FREE_SHRINK)
        low = min((level for level in self.LEVELS if level >= bound), default=self.MAX_ZOOM)
        return min(self.fit_zoom(), low)

    def fit_zoom(self) -> float:
        """Smallest zoom at which the screen still shows no more than the world.

        This is what "fit" means: the whole world, or as much as the screen
        holds, and nothing twice.  1/k is only a fit while the world holds k
        screens' worth of cells both ways.
        """
        need = max(math.ceil(self.screen_w / max(1, self.world_w)),
                   math.ceil(self.screen_h / max(1, self.world_h)))
        if need > 1:
            return min(self.MAX_ZOOM, need)
        best = 1
        for k in SHRINKS:
            if self.screen_w * k > self.world_w or self.screen_h * k > self.world_h:
                break
            best = 1 / k
        return best

    def clamp_zoom(self, zoom: float) -> float:
        """The allowed level nearest ``zoom`` -- by ratio, as zoom is felt."""
        low = self.min_zoom()
        zoom = float(zoom)
        if not zoom > low:                  # also zero, negatives and NaN
            return low
        if zoom >= self.MAX_ZOOM:
            return max(low, self.MAX_ZOOM)
        return min((level for level in self.LEVELS if low <= level <= self.MAX_ZOOM),
                   key=lambda level: abs(math.log(zoom / level)))

    @property
    def shrink(self) -> int:
        """Cells per screen pixel: k at zoom 1/k, otherwise 1."""
        return round(1 / self.zoom) if self.zoom < 1 else 1

    def _cells(self, pixels: float, zoom: float | None = None) -> float:
        """Screen pixels -> cells at ``zoom``; exact at 1/k, where 1/zoom is not."""
        zoom = self.zoom if zoom is None else zoom
        return pixels * round(1 / zoom) if zoom < 1 else pixels / zoom

    def _origin(self) -> tuple[float, float]:
        """The world position drawn at the screen's top-left corner.

        Zoomed out it snaps down to a multiple of the block size, so each
        screen pixel keeps covering the same cells while the camera pans.
        """
        k = self.shrink
        if k == 1:
            return self.x, self.y
        return math.floor(self.x / k) * k, math.floor(self.y / k) * k

    def _moved(self) -> None:
        self.version += 1

    def set_screen(self, width: int, height: int) -> None:
        self.screen_w, self.screen_h = max(1, int(width)), max(1, int(height))
        self.zoom = self.clamp_zoom(self.zoom)
        self._moved()

    def set_world(self, width: int, height: int) -> None:
        self.world_w, self.world_h = int(width), int(height)
        self.zoom = self.clamp_zoom(self.zoom)
        self.x %= self.world_w
        self.y %= self.world_h
        self._moved()

    def center(self) -> None:
        """Put the middle of the world in the middle of the screen."""
        self.x = (self.world_w - self._cells(self.screen_w)) / 2 % self.world_w
        self.y = (self.world_h - self._cells(self.screen_h)) / 2 % self.world_h
        self._moved()

    # -- movement ---------------------------------------------------------
    def pan_cells(self, dx: float, dy: float) -> None:
        """Move the camera by a number of cells; the world is edgeless."""
        self.x = (self.x + dx) % self.world_w
        self.y = (self.y + dy) % self.world_h
        self._moved()

    def pan_pixels(self, dx: float, dy: float) -> None:
        self.pan_cells(self._cells(dx), self._cells(dy))

    def set_zoom(self, zoom: float, anchor: tuple[int, int] | None = None) -> bool:
        """Change zoom, keeping the world point under ``anchor`` in place.

        The anchor maths uses the unsnapped position, so zooming out and back
        in at the same spot returns exactly to where it started.
        """
        new = self.clamp_zoom(zoom)
        if new == self.zoom:
            return False
        if anchor is None:
            anchor = (self.screen_w // 2, self.screen_h // 2)
        ax, ay = anchor
        wx, wy = self.x + self._cells(ax), self.y + self._cells(ay)
        self.zoom = new
        self.x = (wx - self._cells(ax)) % self.world_w
        self.y = (wy - self._cells(ay)) % self.world_h
        self._moved()
        return True

    @staticmethod
    def _notch(zoom: float, step: int) -> float:
        """One wheel notch from ``zoom``: ``step`` is +1 (in) or -1 (out)."""
        if zoom < 1 or (zoom <= 1 and step < 0):
            ladder = (1,) + SHRINKS             # cells per pixel, 1 first
            k = round(1 / zoom)
            here = min(range(len(ladder)), key=lambda i: abs(ladder[i] - k))
            there = max(0, min(len(ladder) - 1, here - step))
            return 1 if there == 0 else 1 / ladder[there]
        if zoom < 4 or (step < 0 and zoom <= 4):
            return zoom + step                  # fine steps where doubling is coarse
        return int(round(zoom * 2 ** step))

    def zoom_by(self, steps: int, anchor: tuple[int, int] | None = None) -> bool:
        """Zoom ``steps`` notches in (positive) or out, level by level.

        In from 1 a notch adds a pixel per cell below 4 and doubles above;
        out from 1 it walks 1/2, 1/3, 1/4, 1/6, 1/8, 1/12, 1/16.
        """
        if not steps:
            return False
        step = 1 if steps > 0 else -1
        target = self.zoom
        for _ in range(min(abs(int(steps)), 2 * len(SHRINKS) + 8)):   # past both ends by then
            target = self._notch(target, step)
        return self.set_zoom(target, anchor)

    def fit(self) -> None:
        self.set_zoom(self.fit_zoom())

    def frame(self, rect: tuple[int, int, int, int]) -> None:
        """Show ``rect`` (x, y, w, h in cells) as large as it fits, centred.

        The largest allowed level at which the whole rectangle is on screen
        wins -- past the fit if that is what it takes, the world then showing
        around it again; if even the smallest cannot hold it, the smallest is
        used and the rectangle's middle is centred anyway.  Zoomed out the view can
        only start on a block boundary, which both the fit and the centring
        take into account -- otherwise the snap could push the rectangle's
        last column or row off screen.
        """
        x, y, w, h = rect
        w, h = max(1, w), max(1, h)
        x, y = x % self.world_w, y % self.world_h
        low = self.min_zoom()
        fits = [z for z in self.LEVELS if low <= z <= self.MAX_ZOOM
                and self._holds(z, self.screen_w, x, w, self.world_w)
                and self._holds(z, self.screen_h, y, h, self.world_h)]
        self.zoom = max(fits) if fits else low
        self.x = self._framed(self.screen_w, x, w, self.world_w)
        self.y = self._framed(self.screen_h, y, h, self.world_h)
        self._moved()

    def _holds(self, zoom: float, screen: int, start: float, size: float, world: int) -> bool:
        """Whether ``screen`` pixels at ``zoom`` can show ``size`` cells from ``start``."""
        cells = self._cells(screen, zoom)
        if zoom < 1 and cells < world:
            size += start % round(1 / zoom)     # the view begins at the block boundary before it
        return cells >= size

    def _framed(self, screen: int, start: float, size: float, world: int) -> float:
        """The camera position along one axis that centres ``size`` cells from ``start``.

        Zoomed out, the view starts on a block boundary -- a multiple of k in
        0..world, repeating every world -- so the position is put on the
        boundary nearest the centred one among those that keep the span on
        screen, where the snap then leaves it exactly.
        """
        cells = self._cells(screen)
        middle = start + size / 2 - cells / 2
        k = self.shrink
        if k == 1:
            return middle % world
        base = math.floor(middle / world) * world
        below = base + math.floor((middle - base) / k) * k
        above = min(below + k, base + world)
        lo, hi = start + size - cells, start                    # origins that keep all of it on screen
        choices = [o for o in (below, above) if lo <= o <= hi] or [below, above]
        return float(min(choices, key=lambda o: abs(o - middle)) % world)

    # -- persistence ------------------------------------------------------
    def state(self) -> dict:
        return {"x": round(self.x, 3), "y": round(self.y, 3), "zoom": self.zoom}

    def restore(self, state: dict) -> None:
        """Bring back a saved view; old saves carry an int zoom, newer ones may be 1/k.

        All or nothing: a state with any unusable value leaves the view as it was.
        """
        try:
            # float() overflows where the int() of old clamped a huge number.
            zoom = self.clamp_zoom(float(state["zoom"]))
            x, y = float(state["x"]), float(state["y"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return
        if not (math.isfinite(x) and math.isfinite(y)):
            return
        self.zoom, self.x, self.y = zoom, x % self.world_w, y % self.world_h
        self._moved()

    # -- coordinate conversion -------------------------------------------
    def unwrapped(self, sx: float, sy: float) -> tuple[int, int]:
        """The cell under a screen pixel *before* wrapping.

        Zoomed out, that is the first cell of the block the pixel shows.
        """
        ox, oy = self._origin()
        return math.floor(ox + self._cells(sx)), math.floor(oy + self._cells(sy))

    def screen_to_world(self, sx: float, sy: float) -> tuple[int, int]:
        """Screen pixel -> world cell (wrapped)."""
        wx, wy = self.unwrapped(sx, sy)
        return wx % self.world_w, wy % self.world_h

    def world_to_screen(self, wx: int, wy: int) -> tuple[int, int]:
        """World cell -> screen pixel of its top-left corner (may be off-screen).

        Zoomed out, the pixel whose block holds the cell.
        """
        ox, oy = self._origin()
        dx = (wx - ox) % self.world_w
        dy = (wy - oy) % self.world_h
        k = self.shrink
        if k > 1:
            if dx > self.world_w - dx and dx > self.screen_w * k:
                dx -= self.world_w
            if dy > self.world_h - dy and dy > self.screen_h * k:
                dy -= self.world_h
            return math.floor(dx / k), math.floor(dy / k)
        if dx > self.world_w - dx and dx * self.zoom > self.screen_w:
            dx -= self.world_w
        if dy > self.world_h - dy and dy * self.zoom > self.screen_h:
            dy -= self.world_h
        return int(round(dx * self.zoom)), int(round(dy * self.zoom))

    # -- framing ----------------------------------------------------------
    def viewport(self) -> Viewport:
        k = self.shrink
        if k > 1:
            ox, oy = self._origin()
            return Viewport(ox % self.world_w, oy % self.world_h,
                            min(self.world_w, self.screen_w * k), min(self.world_h, self.screen_h * k),
                            0, 0, 1, k)
        cell_x, cell_y = math.floor(self.x), math.floor(self.y)
        frac_x, frac_y = self.x - cell_x, self.y - cell_y
        offset_x = -int(round(frac_x * self.zoom))
        offset_y = -int(round(frac_y * self.zoom))
        cols = min(self.world_w, math.ceil((self.screen_w - offset_x) / self.zoom))
        rows = min(self.world_h, math.ceil((self.screen_h - offset_y) / self.zoom))
        return Viewport(cell_x % self.world_w, cell_y % self.world_h,
                        cols, rows, offset_x, offset_y, self.zoom)
