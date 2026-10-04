"""Where on the desktop the wallpaper lives, and proof that it really shows.

There are two shapes of desktop, and the wallpaper has to sit *under the icons*
in both -- never over them, never over an application:

``progman``  Windows 11 24H2 and later.  Progman holds the icon view
             (SHELLDLL_DefView) and paints the Windows wallpaper itself.
             0x052C adds a WorkerW under the icons, an empty stage meant for
             live wallpapers: opaque black when nothing draws on it.  Our
             surfaces become children of Progman just under the icons, and
             that WorkerW is kept hidden (``uncover_wallpaper``), so whenever
             the Game of Life is hidden, closed or even crashes, the user's own
             wallpaper is what shows -- never a black desktop.  Progman has no
             redirection surface, which is why the surfaces are drawn through
             DirectComposition.
``workerw``  Windows 10 and earlier 11 builds.  Sending Progman 0x052C splits
             the desktop into a WorkerW holding the icons and a second WorkerW
             behind it for the wallpaper; our surfaces become children of that
             second one.

``bottom`` -- a top-level window pinned beneath every other window -- is only
ever used when asked for, because it covers the icons.  Every Win32 call here
reports success whether or not DWM shows the result, so ``VisibilityProbe``
checks the actual screen pixels.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ..native import win32 as w
from ..native.window import Window

WM_SPAWN_WORKER = 0x052C
DESKTOP_ROOTS = frozenset({"Progman", "WorkerW"})
SHELL_CLASSES = frozenset({"SHELLDLL_DefView", "WorkerW", "Progman", "SysListView32"})
SURFACE_CLASS = "GolWallpaperSurface"
KINDS = ("progman", "workerw", "bottom")


@dataclass(frozen=True)
class DesktopLayer:
    kind: str           # "progman", "workerw", "bottom" or "window"
    parent: int         # what our surfaces are children of; 0 for top-level ones
    progman: int = 0
    defview: int = 0
    workerw: int = 0

    def describe(self) -> str:
        return {
            "progman": "a child of Progman under the icon view (Windows 11 24H2+ layout)",
            "workerw": "a child of the wallpaper WorkerW behind the icons (classic layout)",
            "bottom": "a bottom-most top-level window (covers the desktop icons)",
            "window": "an ordinary window",
        }.get(self.kind, self.kind)

    def alive(self) -> bool:
        if self.kind == "window":
            return True
        if not self.progman or not w.IsWindow(self.progman):
            return False
        if self.parent and not w.IsWindow(self.parent):
            return False
        return not self.defview or bool(w.IsWindow(self.defview))


def _spans_desktop(hwnd: int) -> bool:
    if not hwnd or not w.IsWindow(hwnd) or not w.IsWindowVisible(hwnd):
        return False
    _, _, vw, vh = w.virtual_screen()
    _, _, cw, ch = w.window_rect(hwnd)
    return cw >= vw * 0.9 and ch >= vh * 0.9


def spawn_workerw(progman: int) -> None:
    """Ask Progman for the wallpaper WorkerW; harmless when it already exists.

    Which (wParam, lParam) does it varies by build, so every known variant is
    sent -- with a timeout, so a hung Explorer cannot hang us too.
    """
    result = ctypes.c_size_t()
    for wparam, lparam in ((0x0D, 0x00), (0x0D, 0x01), (0x00, 0x00)):
        w.SendMessageTimeoutW(progman, WM_SPAWN_WORKER, wparam, lparam,
                              w.SMTO_NORMAL | w.SMTO_ABORTIFHUNG, 500, ctypes.byref(result))


def _classic_layout() -> tuple[int, int, int]:
    """(icons host, its DefView, wallpaper WorkerW) in the pre-24H2 layout."""
    found: list[tuple[int, int, int]] = []

    def callback(hwnd, _lparam):
        defview = w.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
        if defview:
            wall = w.FindWindowExW(None, hwnd, "WorkerW", None)
            found.append((int(hwnd), int(defview), int(wall or 0)))
        return True

    proc = w.WNDENUMPROC(callback)
    w.EnumWindows(proc, 0)
    for host, defview, wall in found:
        if wall and _spans_desktop(wall):
            return host, defview, wall
    return (found[0][0], found[0][1], 0) if found else (0, 0, 0)


def find_layer(mode: str = "auto", allow_partial: bool = False) -> DesktopLayer | None:
    """Locate the desktop's windows, nudging Explorer into creating them.

    ``None`` means there is no desktop to attach to yet -- Explorer is
    starting, restarting or not running -- and the caller should try again.
    In the 24H2 layout the icon view appears a moment after the WorkerW; until
    it does there is nothing to sit *under*, so that counts as not ready too,
    unless ``allow_partial`` says we have waited long enough.
    """
    progman = int(w.FindWindowW("Progman", None) or 0)
    if not progman or not w.IsWindow(progman):
        return None
    if mode == "bottom":
        return DesktopLayer("bottom", 0, progman)
    if w.IsHungAppWindow(progman):
        return None
    spawn_workerw(progman)
    defview = int(w.FindWindowExW(progman, None, "SHELLDLL_DefView", None) or 0)
    child_workerw = int(w.FindWindowExW(progman, None, "WorkerW", None) or 0)
    if mode in ("auto", "progman") and (child_workerw or (defview and mode == "progman")):
        if not defview and not allow_partial:
            return None
        return DesktopLayer("progman", progman, progman, defview, child_workerw)
    host, host_defview, wall = _classic_layout()
    if wall and mode in ("auto", "workerw"):
        return DesktopLayer("workerw", wall, progman, host_defview, wall)
    if defview and mode == "auto" and _spans_desktop(progman):
        # The icons are still in Progman and no WorkerW appeared: the 24H2
        # arrangement minus the wallpaper window.  Under the icons is still
        # the right place.
        return DesktopLayer("progman", progman, progman, defview, 0)
    return None


def uncover_wallpaper(layer: DesktopLayer | None, ours: set[int] | frozenset[int] = frozenset()) -> bool:
    """Hide the empty WorkerW that 0x052C leaves over the wallpaper on 24H2.

    Nothing of ours is in it -- the surfaces are Progman's children -- so it
    only ever hides the Windows wallpaper, and it stays after we are gone.
    Left alone only when another live-wallpaper program is drawing in it.
    True if it was hidden now.
    """
    if layer is None or layer.kind != "progman" or not layer.progman or not w.IsWindow(layer.progman):
        return False
    workerw = int(w.FindWindowExW(layer.progman, None, "WorkerW", None) or 0)
    if not workerw or not w.IsWindowVisible(workerw):
        return False
    others: list[int] = []

    def callback(child, _lparam):
        if int(child) not in ours:
            others.append(int(child))
        return True

    w.EnumChildWindows(workerw, w.WNDENUMPROC(callback), 0)
    if others:
        return False
    w.ShowWindow(workerw, w.SW_HIDE)
    return True


def refresh_layer(layer: DesktopLayer) -> DesktopLayer:
    """The same layer with the icon view and WorkerW looked up again.

    Explorer recreates SHELLDLL_DefView (and, on 24H2, the WorkerW) in the
    course of normal use; holding on to a dead handle would leave nothing to
    keep the wallpaper beneath.
    """
    if layer.kind != "progman" or not w.IsWindow(layer.progman):
        return layer
    defview = int(w.FindWindowExW(layer.progman, None, "SHELLDLL_DefView", None) or 0)
    workerw = int(w.FindWindowExW(layer.progman, None, "WorkerW", None) or 0)
    if (defview, workerw) == (layer.defview, layer.workerw):
        return layer
    return DesktopLayer(layer.kind, layer.parent, layer.progman, defview, workerw)


# -- surfaces -------------------------------------------------------------------
def surface_rect(layer: DesktopLayer, monitor: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Where a surface for ``monitor`` goes, in its parent's coordinates."""
    mx, my, mw, mh = monitor[:4]
    if layer.parent:
        px, py, _, _ = w.window_rect(layer.parent)
        return mx - px, my - py, mw, mh
    return mx, my, mw, mh


def create_surface(layer: DesktopLayer, monitor, on_message: Callable, layered: bool = True) -> Window:
    """A hidden window for one monitor, in its final shape and parent."""
    if layer.kind in ("progman", "workerw"):
        style = w.WS_CHILD | w.WS_CLIPSIBLINGS | w.WS_CLIPCHILDREN
        # NOPARENTNOTIFY: creating or destroying a child otherwise *sends*
        # WM_PARENTNOTIFY to Explorer and waits -- forever, if Explorer hangs.
        ex = w.WS_EX_NOACTIVATE | w.WS_EX_TRANSPARENT | w.WS_EX_NOPARENTNOTIFY
        parent = layer.parent
    else:
        style = w.WS_POPUP
        ex = w.WS_EX_TOOLWINDOW | w.WS_EX_NOACTIVATE | w.WS_EX_NOREDIRECTIONBITMAP
        if layered:
            ex |= w.WS_EX_LAYERED | w.WS_EX_TRANSPARENT      # clicks fall through to the desktop
        parent = None
    window = Window(SURFACE_CLASS, "Game of Life wallpaper", style, ex,
                    surface_rect(layer, monitor), on_message, parent=parent)
    if layer.kind == "bottom" and layered:
        w.SetLayeredWindowAttributes(window.hwnd, 0, 255, w.LWA_ALPHA)
    return window


def _insert_after(layer: DesktopLayer, hwnd: int) -> int | None:
    """The window ours should follow in the Z-order; None when it is already there."""
    if layer.kind == "progman":
        if layer.defview and w.IsWindow(layer.defview):
            return layer.defview                     # directly beneath the icons
        if layer.workerw and w.IsWindow(layer.workerw):
            # No icon view yet: sit directly above the Windows wallpaper, never
            # on top of everything Progman holds.
            above = int(w.GetWindow(layer.workerw, w.GW_HWNDPREV) or 0)
            if above == int(hwnd):
                return None
            return above or w.HWND_TOP
        return w.HWND_TOP
    if layer.kind == "bottom":
        return w.HWND_BOTTOM
    return w.HWND_TOP


def place(layer: DesktopLayer, hwnd: int, monitor, show: bool = True) -> None:
    """Size, position and slot a surface in the Z-order, optionally showing it."""
    x, y, width, height = surface_rect(layer, monitor)
    flags = w.SWP_NOACTIVATE | w.SWP_NOOWNERZORDER | (w.SWP_SHOWWINDOW if show else 0)
    after = _insert_after(layer, hwnd)
    if after is None:
        flags |= w.SWP_NOZORDER
        after = 0
    w.SetWindowPos(hwnd, after, x, y, width, height, flags)
    if layer.kind == "bottom":
        w.mark_not_fullscreen(hwnd)


def restack(layer: DesktopLayer, hwnd: int) -> bool:
    """Put a surface back in its slot if Explorer moved things; True if it had to."""
    if zorder_ok(layer, hwnd):
        return False
    after = _insert_after(layer, hwnd)
    if after is None:
        return False
    w.SetWindowPos(hwnd, after, 0, 0, 0, 0,
                   w.SWP_NOMOVE | w.SWP_NOSIZE | w.SWP_NOACTIVATE | w.SWP_NOOWNERZORDER)
    return True


def zorder_ok(layer: DesktopLayer, hwnd: int) -> bool:
    """Is the surface below the icon view and above the Windows wallpaper?"""
    if layer.kind != "progman":
        return True
    if layer.defview and w.IsWindow(layer.defview):
        h, above_ok = w.GetWindow(hwnd, w.GW_HWNDPREV), False
        for _ in range(64):
            if not h:
                break
            if int(h) == layer.defview:
                above_ok = True
                break
            h = w.GetWindow(h, w.GW_HWNDPREV)
        if not above_ok:
            return False
    if layer.workerw and w.IsWindow(layer.workerw):
        h = w.GetWindow(hwnd, w.GW_HWNDNEXT)
        for _ in range(64):
            if not h:
                return False
            if int(h) == layer.workerw:
                return True
            h = w.GetWindow(h, w.GW_HWNDNEXT)
        return False
    return True


def competing_wallpapers(layer: DesktopLayer, ours: set[int]) -> list[str]:
    """Other programs drawing in the same slot, which would fight us for it."""
    if not layer.parent:
        return []
    names, child = [], w.GetWindow(layer.parent, w.GW_CHILD)
    while child:
        name = w.class_name(child)
        if (name not in SHELL_CLASSES and int(child) not in ours and name != SURFACE_CLASS
                and w.IsWindowVisible(child)):
            names.append(w.window_text(child) or name)
        child = w.GetWindow(child, w.GW_HWNDNEXT)
    return names


# -- proof ----------------------------------------------------------------------
PROBE_A = (40, 8, 32)
PROBE_B = (8, 36, 16)
PROBE_DELTA = 12              # minimum change between the two captures, per channel


def desktop_root(hwnd: int, ours: frozenset[int] | set[int] = frozenset()) -> bool:
    return bool(hwnd) and (int(hwnd) in ours or w.class_name(hwnd) in DESKTOP_ROOTS)


def capture(points: list[tuple[int, int]]) -> np.ndarray:
    """Screen colours at ``points`` as an (n, 3) RGB array.

    The probe lattice lies on a few dozen rows, so each row is read as a strip
    one pixel high: a handful of small blits instead of one bitmap the size of
    every monitor put together.
    """
    out = np.zeros((len(points), 3), np.uint8)
    if not points:
        return out
    screen = w.GetDC(None)
    if not screen:
        return out[:0]
    mem = w.CreateCompatibleDC(screen)
    rows: dict[int, list[int]] = {}
    for index, (_, y) in enumerate(points):
        rows.setdefault(y, []).append(index)
    widest = max(max(points[i][0] for i in idx) - min(points[i][0] for i in idx) + 1
                 for idx in rows.values())
    info = w.BITMAPINFO()
    head = info.bmiHeader
    head.biSize = ctypes.sizeof(w.BITMAPINFOHEADER)
    head.biWidth, head.biHeight = widest, -1
    head.biPlanes, head.biBitCount, head.biCompression = 1, 32, w.BI_RGB
    bits = w.LPVOID()
    bitmap = w.CreateDIBSection(mem, ctypes.byref(info), w.DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
    try:
        if not bitmap or not bits:
            return out[:0]
        old = w.SelectObject(mem, bitmap)
        strip = np.ctypeslib.as_array(ctypes.cast(bits, ctypes.POINTER(ctypes.c_uint8)), shape=(widest, 4))
        try:
            for y, indices in rows.items():
                left = min(points[i][0] for i in indices)
                width = max(points[i][0] for i in indices) - left + 1
                w.BitBlt(mem, 0, 0, width, 1, screen, left, y, w.SRCCOPY)
                for i in indices:
                    out[i] = strip[points[i][0] - left, [2, 1, 0]]
        finally:
            w.SelectObject(mem, old)
        return out
    finally:
        if bitmap:
            w.DeleteObject(bitmap)
        w.DeleteDC(mem)
        w.ReleaseDC(None, screen)


class VisibilityProbe:
    """Proves the wallpaper's pixels are on screen, or proves they are not.

    The renderer draws two different near-black markers, in 8x8 squares on a
    64-pixel lattice, for a couple of frames each -- invisible in practice --
    and the screen is read back where the desktop is genuinely uncovered.
    When every monitor is covered by windows the honest answer is "cannot
    tell", and saying so is the point of the third state.
    """

    def __init__(self, minimum_points: int = 12, share: float = 0.4, limit: int = 160) -> None:
        self.minimum_points = minimum_points
        self.share = share
        self.limit = limit
        self.last_points = 0
        self.last_hits = 0

    def sample_points(self, rects: list[tuple[int, int, int, int]],
                      ours: frozenset[int] | set[int] = frozenset()) -> list[tuple[int, int]]:
        lattice = []
        for x, y, width, height in rects:
            for py in range(4, height, 64):
                for px in range(4, width, 64):
                    lattice.append((x + px, y + py))
        if len(lattice) > self.limit * 4:
            lattice = lattice[::max(1, len(lattice) // (self.limit * 4))]
        out = [p for p in lattice if desktop_root(w.root_at(*p), ours)]
        if len(out) > self.limit:
            out = out[::max(1, len(out) // self.limit)][:self.limit]
        return out

    def judge(self, first: np.ndarray, second: np.ndarray) -> bool:
        """A point is ours when it changed between the frames the way the markers did.

        Comparing the two captures with each other, rather than with the exact
        marker colours, survives HDR, Auto Color Management and night-light
        style transforms, which move absolute values but keep the direction:
        red and blue fall from the first marker to the second, green rises.
        """
        a, b = first.astype(int), second.astype(int)
        red, green, blue = a[:, 0] - b[:, 0], b[:, 1] - a[:, 1], a[:, 2] - b[:, 2]
        ours = (red >= PROBE_DELTA) & (green >= PROBE_DELTA) & (blue >= PROBE_DELTA // 2)
        self.last_hits = int(np.count_nonzero(ours))
        return self.last_hits >= max(3, self.last_points * self.share)

    def run(self, points: list[tuple[int, int]], show: Callable[[tuple[int, int, int] | None], None],
            settle: Callable[[], None]) -> bool | None:
        """True if visible, False if provably not, None if it cannot be told."""
        self.last_points, self.last_hits = len(points), 0
        if len(points) < self.minimum_points:
            return None
        try:
            show(PROBE_A)
            settle()
            first = capture(points)
            show(PROBE_B)
            settle()
            second = capture(points)
        finally:
            show(None)
        if len(first) != len(points) or len(second) != len(points):
            return None
        return self.judge(first, second)

    def describe(self) -> str:
        if not self.last_points:
            return "no uncovered desktop to measure on"
        return f"{self.last_hits} of {self.last_points} sampled desktop points were ours"


def settle_composition(frames: int = 2) -> None:
    """Wait for DWM to compose what was just presented."""
    for _ in range(frames):
        if w.DwmFlush() != 0:
            break
