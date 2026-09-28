"""Should the wallpaper be working right now, and how hard?

A wallpaper nobody can see should cost nothing.  Four independent signals feed
that decision, each kept separate so it can be tested on its own:

``PowerPolicy``  a full-screen game or presentation is in front -- stay out of
                 its way entirely.
``Occlusion``    every monitor's desktop is covered by windows -- nothing to
                 draw.  Most of the day, with maximised windows, this is true.
``SystemState``  the session is locked, the display is off, the machine is
                 asleep or being used over Remote Desktop, or battery saver is
                 on -- all reported by Windows as notifications, not polled.

Two traps shaped ``PowerPolicy``: the desktop window spans every monitor, so a
naive "is the foreground window the size of its monitor?" test treats clicking
the desktop as a full-screen game; and a wallpaper that covers every monitor
can make the shell report ``QUNS_BUSY`` all by itself.  Both shipped once.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from ..native import win32 as w

# Foreground windows that must never count as "a full-screen app is running".
NEVER_FULLSCREEN = frozenset({
    "Progman", "WorkerW", "SHELLDLL_DefView", "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow",
    "TopLevelWindowForOverflowXamlIsland", "NotifyIconOverflowWindow",
})

QUNS_BUSY = 2
QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4


@dataclass(frozen=True)
class Verdict:
    run: bool
    reason: str

    def __bool__(self) -> bool:
        return self.run


class PowerPolicy:
    """Decides whether to keep going with a full-screen app in front."""

    def __init__(self, pause_when_busy: bool = True,
                 own_window_classes: frozenset[str] = frozenset()) -> None:
        self.pause_when_busy = pause_when_busy
        self.ours = frozenset(own_window_classes)

    def shell_state(self) -> int:
        state = ctypes.c_int(0)
        if w.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return 0
        return state.value

    def foreground_class(self) -> str:
        hwnd = w.GetForegroundWindow()
        if not hwnd:
            return ""
        return w.class_name(w.GetAncestor(hwnd, w.GA_ROOT))

    def foreground_covers_monitor(self) -> bool:
        hwnd = w.GetForegroundWindow()
        if not hwnd:
            return False
        hwnd = w.GetAncestor(hwnd, w.GA_ROOT) or hwnd
        left, top, right, bottom = w.visible_frame(hwnd)
        info = w.MONITORINFO()
        info.cbSize = ctypes.sizeof(w.MONITORINFO)
        monitor = w.MonitorFromWindow(hwnd, 2)          # MONITOR_DEFAULTTONEAREST
        if not monitor or not w.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False
        m = info.rcMonitor
        return left <= m.left and top <= m.top and right >= m.right and bottom >= m.bottom

    def foreground_is_a_real_fullscreen_app(self) -> bool:
        name = self.foreground_class()
        if not name or name in NEVER_FULLSCREEN or name in self.ours:
            return False
        return self.foreground_covers_monitor()

    def check(self) -> Verdict:
        if not self.pause_when_busy:
            return Verdict(True, "pausing is switched off")
        state = self.shell_state()
        if state == QUNS_RUNNING_D3D_FULL_SCREEN:
            return Verdict(False, "a full-screen Direct3D application is running")
        if state == QUNS_PRESENTATION_MODE:
            return Verdict(False, "presentation mode is on")
        # Both signals are needed.  QUNS_BUSY alone can be set off by a window
        # of our own; "covers its monitor" alone is also true of any maximised
        # window when the taskbar auto-hides -- which would pause the wallpaper
        # on every other monitor too.  The shell only says BUSY for real
        # full-screen apps (F11 browsers, borderless games, videos).
        if state == QUNS_BUSY and self.foreground_is_a_real_fullscreen_app():
            return Verdict(False, f"{self.foreground_class()} is full screen")
        return Verdict(True, "nothing in the way")


# -- occlusion ---------------------------------------------------------------------
Rect = tuple[int, int, int, int]        # left, top, right, bottom


def subtract(pieces: list[Rect], cut: Rect) -> list[Rect]:
    """Remove ``cut`` from a set of disjoint rectangles."""
    cl, ct, cr, cb = cut
    out: list[Rect] = []
    for left, top, right, bottom in pieces:
        if cr <= left or cl >= right or cb <= top or ct >= bottom:
            out.append((left, top, right, bottom))
            continue
        if top < ct:
            out.append((left, top, right, ct))
        if cb < bottom:
            out.append((left, cb, right, bottom))
        it, ib = max(top, ct), min(bottom, cb)
        if left < cl:
            out.append((left, it, cl, ib))
        if cr < right:
            out.append((cr, it, right, ib))
    return out


def uncovered(monitors: list[Rect], windows: list[Rect], min_side: int = 24) -> list[bool]:
    """For each monitor, whether some worthwhile patch of desktop is left showing.

    A sliver beside a maximised window -- the few pixels a snapped layout or
    an auto-hidden taskbar leaves -- is not worth animating a whole monitor for.
    """
    result = []
    for monitor in monitors:
        pieces = [monitor]
        for rect in windows:
            pieces = subtract(pieces, rect)
            if not pieces or len(pieces) > 400:
                break
        result.append(len(pieces) > 400 or any(r - l >= min_side and b - t >= min_side
                                               for l, t, r, b in pieces))
    return result


class Occlusion:
    """Works out which monitors still show any desktop."""

    STOP_AT = frozenset({"Progman", "WorkerW"})
    ALWAYS_COVERS = frozenset({"Shell_TrayWnd", "Shell_SecondaryTrayWnd"})

    def __init__(self, own_hwnds: set[int] | None = None) -> None:
        self.own = own_hwnds if own_hwnds is not None else set()

    def _counts(self, hwnd: int, name: str) -> bool:
        if name in self.ALWAYS_COVERS:
            return True
        if int(hwnd) in self.own or w.IsIconic(hwnd) or w.is_cloaked(hwnd):
            return False
        ex = w.ex_style(hwnd)
        if ex & w.WS_EX_TRANSPARENT:
            return False                  # click-through overlays
        if ex & w.WS_EX_LAYERED:
            key, alpha, flags = wintypes.COLORREF(0), ctypes.c_ubyte(0), wintypes.DWORD(0)
            ok = w.GetLayeredWindowAttributes(hwnd, ctypes.byref(key), ctypes.byref(alpha),
                                              ctypes.byref(flags))
            if not ok or not (flags.value & w.LWA_ALPHA) or alpha.value < 250:
                return False              # see-through, or per-pixel alpha we cannot judge
        return True

    def covering_windows(self) -> list[Rect]:
        found: list[Rect] = []

        def callback(hwnd, _lparam):
            if not w.IsWindowVisible(hwnd):
                return True
            name = w.class_name(hwnd)
            if name in self.STOP_AT and w.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None):
                return False              # everything below this is the desktop itself
            if name == "Progman":
                return False
            if self._counts(hwnd, name):
                left, top, right, bottom = w.visible_frame(hwnd)
                if right > left and bottom > top:
                    found.append((left, top, right, bottom))
            return True

        proc = w.WNDENUMPROC(callback)
        w.EnumWindows(proc, 0)
        return found

    def visible(self, monitors: list[Rect]) -> list[bool]:
        return uncovered(monitors, self.covering_windows())


# -- what Windows tells us ---------------------------------------------------------
@dataclass
class SystemState:
    locked: bool = False
    display_off: bool = False
    asleep: bool = False
    remote: bool = False
    on_battery: bool = False
    saver: bool = False

    def refresh_static(self) -> None:
        status = w.power_status()
        if status is not None:
            self.on_battery = status.ACLineStatus == 0
            self.saver = bool(status.SystemStatusFlag & 1)
        self.remote = bool(w.GetSystemMetrics(w.SM_REMOTESESSION))

    def on_power_setting(self, lparam: int) -> str | None:
        """Apply a PBT_POWERSETTINGCHANGE notification; returns what changed."""
        setting = ctypes.cast(lparam, ctypes.POINTER(w.POWERBROADCAST_SETTING)).contents
        value = setting.Data[0] if setting.DataLength >= 1 else 0
        guid = setting.PowerSetting
        if guid == w.GUID_CONSOLE_DISPLAY_STATE:
            self.display_off = value == 0
            return "display " + ("off" if value == 0 else "dimmed" if value == 2 else "on")
        if guid == w.GUID_ACDC_POWER_SOURCE:
            self.on_battery = value != 0
            return "on battery" if value else "on mains power"
        if guid == w.GUID_POWER_SAVING_STATUS:
            self.saver = value != 0
            return "battery saver " + ("on" if value else "off")
        return None

    def on_session(self, event: int) -> str | None:
        if event == w.WTS_SESSION_LOCK:
            self.locked = True
            return "session locked"
        if event == w.WTS_SESSION_UNLOCK:
            self.locked = False
            return "session unlocked"
        if event in (w.WTS_REMOTE_CONNECT, w.WTS_REMOTE_DISCONNECT,
                     w.WTS_CONSOLE_CONNECT, w.WTS_CONSOLE_DISCONNECT):
            self.remote = bool(w.GetSystemMetrics(w.SM_REMOTESESSION))
            return "remote session" if self.remote else "local session"
        return None
