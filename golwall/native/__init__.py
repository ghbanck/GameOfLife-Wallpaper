"""Thin bindings to Windows, and nothing else.

Everything here is a direct wrapper over a Win32, GDI, D3D11, DXGI or
DirectComposition call, or a small convenience over one.  No decisions live
at this level -- which layer to draw into, when to pause, whether a window
counts as full screen, all of that is a capability.
"""

from . import win32
from .window import Window

__all__ = ["win32", "Window"]
