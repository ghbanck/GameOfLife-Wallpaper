"""A bare Win32 window whose messages arrive as a Python callback.

The subtle part is lifetime.  A window class is registered once per process and
keeps a pointer to whatever ``WNDPROC`` it was registered with -- so binding
that pointer to a particular window's callback is a trap: destroy and recreate
a window of the same class and the class still points at the first one's
callback, which Python has since collected.

So each class gets one dispatcher that outlives every window, and it routes by
handle to whichever ``Window`` is alive now.  Windows can also be destroyed out
from under us -- a child of Explorer's desktop dies with Explorer -- so the
dispatcher notices ``WM_NCDESTROY`` and forgets the handle either way.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable

from . import win32 as w

_WINDOWS: dict[int, "Window"] = {}
_CLASS_PROCS: dict[str, w.WNDPROC] = {}     # never collected, by design
_CREATING: list["Window"] = []

# Exceptions must never unwind into Win32, but swallowing them silently means a
# packaged build fails invisibly.  Whoever owns the app installs a reporter.
report_error: Callable[[str], None] | None = None


def _dispatch(hwnd, msg, wparam, lparam):
    window = _WINDOWS.get(int(hwnd or 0))
    if window is None and _CREATING:
        # Messages sent from inside CreateWindowEx arrive before we know the
        # handle; they belong to the window being built right now.
        window = _CREATING[-1]
    result = None
    if window is not None:
        try:
            result = window.on_message(hwnd, msg, wparam, lparam)
        except Exception:
            import traceback
            text = traceback.format_exc()
            if report_error is not None:
                try:
                    report_error(f"error handling message 0x{msg:04X}: {text}")
                except Exception:
                    pass
            else:
                traceback.print_exc()
            result = None
        if msg == w.WM_NCDESTROY:
            _WINDOWS.pop(int(hwnd or 0), None)
            window.hwnd = None
            window.alive = False
    if result is not None:
        return result
    return w.DefWindowProcW(hwnd, msg, wparam, lparam)


def _ensure_class(class_name: str, hinstance: int, style: int) -> None:
    if class_name in _CLASS_PROCS:
        return
    proc = w.WNDPROC(_dispatch)
    cls = w.WNDCLASSEXW()
    cls.cbSize = ctypes.sizeof(w.WNDCLASSEXW)
    cls.style = style
    cls.lpfnWndProc = proc
    cls.hInstance = hinstance
    cls.hCursor = w.LoadCursorW(None, ctypes.c_wchar_p(w.IDC_ARROW))
    cls.lpszClassName = class_name
    if not w.RegisterClassExW(ctypes.byref(cls)):
        err = ctypes.get_last_error()
        if err != w.ERROR_CLASS_ALREADY_EXISTS:
            raise ctypes.WinError(err)
    _CLASS_PROCS[class_name] = proc              # keep the pointer alive forever


class Window:
    def __init__(self, class_name: str, title: str, style: int, ex_style: int,
                 rect: tuple[int, int, int, int],
                 on_message: Callable[[int, int, int, int], int | None],
                 parent: int | None = None, class_style: int = 0) -> None:
        self.class_name = class_name
        self.on_message = on_message
        self.hinstance = w.GetModuleHandleW(None)
        self.alive = False
        _ensure_class(class_name, self.hinstance, class_style)

        # A window must be created as a child if it is ever going to be one:
        # converting a top-level window afterwards leaves it out of the
        # compositor, which stays invisible until you measure actual pixels.
        x, y, width, height = rect
        _CREATING.append(self)
        try:
            self.hwnd = w.CreateWindowExW(ex_style, class_name, title, style,
                                          x, y, width, height, parent, None,
                                          self.hinstance, None)
        finally:
            _CREATING.pop()
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self.alive = True
        _WINDOWS[int(self.hwnd)] = self

    def exists(self) -> bool:
        return bool(self.hwnd) and bool(w.IsWindow(self.hwnd))

    def show(self, visible: bool = True) -> None:
        if self.hwnd:
            w.ShowWindow(self.hwnd, w.SW_SHOWNA if visible else w.SW_HIDE)

    def destroy(self) -> None:
        hwnd, self.hwnd = self.hwnd, None
        self.alive = False
        if hwnd:
            _WINDOWS.pop(int(hwnd), None)
            if w.IsWindow(hwnd):
                w.DestroyWindow(hwnd)
