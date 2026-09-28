"""Letting the user draw on the desktop -- and only on the desktop.

While the editor is open, a click that lands on the uncovered desktop (the
icon view, Progman, the WorkerW behind it) is the wallpaper's; a click on any
application, the taskbar or the Start menu is left alone, exactly as if the
editor were closed.  That per-click decision needs a low-level mouse hook,
because the desktop belongs to Explorer and its clicks never reach our windows.

A low-level hook is dangerous in three ways, and all of them shaped this module:

* Windows calls it for *every* mouse event on the machine and waits for the
  answer, so a slow hook makes the whole system's mouse stutter -- and past
  ``LowLevelHooksTimeout`` Windows silently removes it.  So it runs on a thread
  of its own that does nothing else, decides with one ``WindowFromPoint`` on
  button presses only, and hands events to the main thread through a deque.
* Swallowing a mouse *move* freezes the pointer.  Moves are never swallowed;
  they are only recorded while a captured press is held, to draw strokes.
* A gesture whose release never arrives -- a UAC prompt or Ctrl+Alt+Del in the
  middle of a press, an event Windows skipped -- must not leave the hook
  thinking a button is still down, or it would go on swallowing clicks
  everywhere.  Any press that contradicts the gesture ends it.

A press captured on the desktop owns the whole gesture: its release is
swallowed too, even if the pointer has wandered over a window by then, so no
application ever sees half a click.
"""

from __future__ import annotations

import ctypes
import threading
import time
from collections import deque
from collections.abc import Callable
from ctypes import wintypes

from ..native import win32 as w

MOVE, DOWN, UP, WHEEL = "move", "down", "up", "wheel"
LEFT, RIGHT, MIDDLE = 1, 2, 4
BUTTONS = (LEFT, RIGHT, MIDDLE)

_DOWNS = {w.WM_LBUTTONDOWN: LEFT, w.WM_RBUTTONDOWN: RIGHT, w.WM_MBUTTONDOWN: MIDDLE}
_UPS = {w.WM_LBUTTONUP: LEFT, w.WM_RBUTTONUP: RIGHT, w.WM_MBUTTONUP: MIDDLE}


class GestureFilter:
    """The per-event decision, kept free of Windows so it can be tested.

    ``over_desktop(x, y)`` answers whether a point is on the uncovered desktop;
    ``feed`` returns True when the event must be hidden from everything else.
    """

    def __init__(self, over_desktop: Callable[[int, int], bool]) -> None:
        self.over_desktop = over_desktop
        self.held = 0                       # buttons pressed inside a captured gesture
        self.events: deque = deque(maxlen=8192)

    def _end(self, x: int, y: int) -> None:
        """Close a gesture whose releases we never saw, so the editor lets go too."""
        for button in BUTTONS:
            if self.held & button:
                self.events.append((UP, x, y, button))
        self.held = 0

    def feed(self, message: int, x: int, y: int, wheel: int = 0) -> bool:
        if message == w.WM_MOUSEMOVE:
            if self.held:
                self.events.append((MOVE, x, y, self.held))
            return False
        button = _DOWNS.get(message)
        if button is not None:
            if self.held:
                if self.held & button:
                    self._end(x, y)         # pressed again: its release was lost
                elif self.over_desktop(x, y):
                    self.held |= button     # a second button on the desktop joins in
                    self.events.append((DOWN, x, y, button))
                    return True
                else:
                    self._end(x, y)         # the gesture must have ended unseen
                    return False
            if not self.over_desktop(x, y):
                return False
            self.held |= button
            self.events.append((DOWN, x, y, button))
            return True
        button = _UPS.get(message)
        if button is not None:
            if not self.held & button:
                return False
            self.held &= ~button
            self.events.append((UP, x, y, button))
            return True
        if message == w.WM_MOUSEWHEEL:
            if not self.held and not self.over_desktop(x, y):
                return False
            self.events.append((WHEEL, x, y, wheel))
            return True
        return False

    def cancel(self) -> None:
        self.held = 0


class _HookThread:
    """Everything that belongs to one run of the hook thread."""

    def __init__(self) -> None:
        self.thread: threading.Thread | None = None
        self.thread_id = 0
        self.hook = None
        self.ready = threading.Event()
        self.stop = False


class DesktopMouse:
    """Runs the hook on its own thread while the editor is open."""

    def __init__(self, is_desktop: Callable[[int], bool], wake: Callable[[], None] | None = None) -> None:
        self.is_desktop = is_desktop        # root HWND -> is it the desktop (or ours)?
        self.wake = wake or (lambda: None)
        self.filter = GestureFilter(self._over_desktop)
        self._proc = w.HOOKPROC(self._callback)       # must outlive every hook
        self._run_state: _HookThread | None = None
        self._lock = threading.Lock()
        self._roots: dict[int, bool] = {}
        # Checked on every event: even a hook we somehow lost track of stops
        # swallowing the moment the editor closes.
        self.enabled = False
        self.last_event = 0.0
        self.failures = 0

    @property
    def events(self) -> deque:
        return self.filter.events

    @property
    def active(self) -> bool:
        state = self._run_state
        return (state is not None and state.thread is not None and state.thread.is_alive()
                and state.hook is not None)

    def _over_desktop(self, x: int, y: int) -> bool:
        hwnd = w.WindowFromPoint(wintypes.POINT(x, y))
        if not hwnd:
            return False
        root = int(w.GetAncestor(hwnd, w.GA_ROOT) or hwnd)
        known = self._roots.get(root)
        if known is None:
            if len(self._roots) > 256:
                self._roots.clear()
            known = self._roots[root] = bool(self.is_desktop(root))
        return known

    def _callback(self, code, wparam, lparam):
        try:
            if code == w.HC_ACTION and self.enabled:
                self.last_event = time.monotonic()
                message = int(wparam)
                if message in (w.WM_MOUSEMOVE, w.WM_MOUSEWHEEL) or message in _DOWNS or message in _UPS:
                    info = ctypes.cast(lparam, ctypes.POINTER(w.MSLLHOOKSTRUCT)).contents
                    wheel = 0
                    if message == w.WM_MOUSEWHEEL:
                        wheel = ctypes.c_short((info.mouseData >> 16) & 0xFFFF).value
                    if self.filter.feed(message, info.pt.x, info.pt.y, wheel):
                        if message != w.WM_MOUSEMOVE:
                            self.wake()
                        return 1
        except Exception:
            self.failures += 1                # never let an exception reach Windows
        return w.CallNextHookEx(None, code, wparam, lparam)

    def _run(self, state: _HookThread) -> None:
        msg = wintypes.MSG()
        w.PeekMessageW(ctypes.byref(msg), None, 0, 0, w.PM_NOREMOVE)     # create the queue
        state.thread_id = w.GetCurrentThreadId()
        hook = w.SetWindowsHookExW(w.WH_MOUSE_LL, self._proc, w.GetModuleHandleW(None), 0)
        state.hook = hook or None
        state.ready.set()
        if not hook:
            return
        try:
            if not state.stop:                # stop() may have come before we were ready
                while w.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    w.TranslateMessage(ctypes.byref(msg))
                    w.DispatchMessageW(ctypes.byref(msg))
        finally:
            w.UnhookWindowsHookEx(hook)
            state.hook = None

    def start(self) -> bool:
        with self._lock:
            if self.active:
                self.enabled = True
                return True
            self._stop_locked()
            self._roots.clear()
            self.filter.cancel()
            state = _HookThread()
            state.thread = threading.Thread(target=self._run, args=(state,), name="golwall-mouse",
                                            daemon=True)
            self._run_state = state
            state.thread.start()
        state.ready.wait(2.0)
        self.enabled = state.hook is not None
        return self.enabled

    def _stop_locked(self) -> None:
        state = self._run_state
        if state is None:
            return
        state.stop = True
        state.ready.wait(2.0)
        if state.thread_id:
            w.PostThreadMessageW(state.thread_id, w.WM_QUIT, 0, 0)
        if state.thread is not None:
            state.thread.join(2.0)
        if state.thread is None or not state.thread.is_alive():
            self._run_state = None            # otherwise keep it: a later stop retries

    def stop(self) -> None:
        self.enabled = False
        with self._lock:
            self._stop_locked()
        self.filter.cancel()

    def restart(self) -> bool:
        """Reinstall the hook -- for when Windows dropped it after a timeout."""
        self.stop()
        return self.start()
