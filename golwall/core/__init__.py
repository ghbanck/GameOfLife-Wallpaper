"""The simulation and everything that can be reasoned about without Windows.

Nothing in here imports ctypes or touches a window.  That is the point: the
rule engine, the pattern library and formats, the palettes and the camera are
all exercised by the test suite with no display at all.
"""

from .camera import Camera, Viewport
from .life import StepStats, World
from .look import Look
from .palettes import Palette

__all__ = ["Camera", "Viewport", "World", "StepStats", "Palette", "Look"]
