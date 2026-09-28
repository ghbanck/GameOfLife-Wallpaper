"""What the cells look like: the palette tables and how they change over time.

The renderer colours every cell on the GPU from one 512-entry table -- 256
trail levels for dead cells followed by 256 ages for live ones -- so all the
CPU has to do is keep that table current: build it when the palette changes,
blend two of them during a crossfade, and say how fast trails decay.
"""

from __future__ import annotations

import numpy as np

from . import palettes
from .palettes import Palette


class Look:
    def __init__(self, palette: Palette, age_span: int = 20,
                 trail_seconds: float = 1.6) -> None:
        self.age_span = int(age_span)
        self.trail_seconds = max(0.0, float(trail_seconds))
        self.palette = palette
        self._luts = palettes.build_luts(palette, self.age_span)
        self._from = self._to = self._luts
        self._fade_t, self._fade_speed = 1.0, 0.0
        self.version = 0
        self.table = self._combine(self._luts)

    @staticmethod
    def _combine(luts: tuple[np.ndarray, np.ndarray]) -> np.ndarray:
        alive, trail = luts
        return np.ascontiguousarray(np.concatenate([trail, alive], axis=0))

    def _publish(self) -> None:
        self.table = self._combine(self._luts)
        self.version += 1

    # -- palettes -------------------------------------------------------------
    def set_palette(self, palette: Palette, fade_seconds: float = 2.0) -> None:
        """Switch palette, crossfading over ``fade_seconds``."""
        self.palette = palette
        self._from = self._luts
        self._to = palettes.build_luts(palette, self.age_span)
        if fade_seconds <= 0:
            self._luts, self._fade_t, self._fade_speed = self._to, 1.0, 0.0
            self._publish()
        else:
            self._fade_t, self._fade_speed = 0.0, 1.0 / fade_seconds

    def set_age_span(self, age_span: int) -> None:
        self.age_span = max(1, int(age_span))
        self.set_palette(self.palette, 0.0)

    @property
    def fading(self) -> bool:
        return bool(self._fade_speed)

    def advance(self, dt: float) -> bool:
        """Move a crossfade along; True if the table changed."""
        if not self._fade_speed or dt <= 0:
            return False
        self._fade_t += self._fade_speed * dt
        if self._fade_t >= 1.0:
            self._luts, self._fade_t, self._fade_speed = self._to, 1.0, 0.0
        else:
            self._luts = palettes.lerp_luts(self._from, self._to, self._fade_t)
        self._publish()
        return True

    # -- trails ---------------------------------------------------------------
    def decay(self, dt: float) -> float:
        """How much of a trail survives ``dt`` seconds: ~2% after trail_seconds."""
        if self.trail_seconds <= 0:
            return 0.0
        return float(0.02 ** (max(0.0, dt) / self.trail_seconds))

    def accent(self) -> tuple[int, int, int]:
        """The palette's signature colour, for the editor frame and the panel."""
        return self.palette.mature
