"""The scheduler: it owns state and time, and delegates everything else.

One thread -- this one -- owns the world, the renderer, the desktop surfaces,
the tray icon and the hotkey.  Two helpers run beside it and never touch any of
that directly: the control panel's Tk thread and, while the editor is open,
the mouse-hook thread.  Both hand work over through ``call`` and the hook's
event queue, and the main loop picks it up between frames.

The loop sleeps until the next thing it has to do -- a generation, a frame, a
check -- or until a message arrives, whichever is first.  When the wallpaper
cannot be seen at all (covered, locked, display off, a game in front) it does
neither generations nor frames and wakes once a second to look again.
"""

from __future__ import annotations

import ctypes
import logging
import math
import os
import time
from collections import deque
from ctypes import wintypes

import numpy as np

from .capabilities import host, persistence, worlds
from .capabilities.input import DOWN, MOVE, UP, WHEEL, LEFT, MIDDLE, RIGHT, DesktopMouse
from .capabilities.persistence import Config, parse_hotkey
from .capabilities.power import Occlusion, PowerPolicy, SystemState, Verdict
from .core import library, palettes, rle
from .core.camera import Camera
from .core.grid import Grid
from .core.life import World, normalise_rule
from .core.look import Look
from .native import win32 as w
from .native import window as window_module
from .native.window import Window
from .render.renderer import DeviceLost, FrameSpec, Renderer
from .ui.editor import Editor
from .ui.panel import PanelHost
from .ui.text import language as text_language, num, t
from .ui.tray import WM_TRAY

HOTKEY_ID = 0xB01D
MAX_WORLD = 8192                     # cells on a side; the renderer's textures allow no more
WM_WAKE = w.WM_APP + 2
WM_OPEN_EDITOR = w.WM_APP + 3
MODAL_TIMER = 0x60
CONTROL_CLASS = "GolWallpaperControl"
WINDOW_CLASS = "GolWallpaperWindow"
OUR_CLASSES = frozenset({CONTROL_CLASS, WINDOW_CLASS, host.SURFACE_CLASS, "TkTopLevel"})
OTHER_LAYOUT = {"progman": "workerw", "workerw": "progman"}


def _number(value, kind, default, low=None, high=None):
    """``value`` as an int or float within [low, high], or ``default`` if it is not one."""
    try:
        number = kind(value)
        if kind is float and not math.isfinite(number):
            return default
    except (TypeError, ValueError, OverflowError):
        return default
    if low is not None:
        number = max(low, number)
    if high is not None:
        number = min(high, number)
    return number


def _rect(value) -> tuple[int, int, int, int] | None:
    """A (x, y, w, h) rectangle from a world file, or None if it is not one."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    numbers = [_number(v, int, None, -MAX_WORLD, MAX_WORLD) for v in value]
    if None in numbers or numbers[2] < 1 or numbers[3] < 1:
        return None
    return tuple(numbers)


class Wallpaper:
    def __init__(self, cfg: Config, windowed: bool = False, log: logging.Logger | None = None) -> None:
        self.cfg = cfg
        self.windowed = windowed
        self.log = log or logging.getLogger("golwall")
        window_module.report_error = lambda text: self.log.error(text)

        self.running = False
        self._closed = False
        self.paused = False
        self.gps = float(cfg.generations_per_second)
        self.budget = 0.0
        self.skip_remaining = 0
        self.skip_rate = 0.0
        self.interactive = False
        self.wallpaper_visible = True
        self.rest_reason = ""
        self.message = ""
        self.message_serial = 0
        self.hotkey_spec = ""
        self.fps = 0.0
        self._commands: deque = deque()
        self._startup = persistence.startup_enabled()
        self._msg = wintypes.MSG()

        # -- the world ------------------------------------------------------
        library.USER_DIR = persistence.data_dir() / "patterns"
        self.world = World(cfg.world_width, cfg.world_height, cfg.rule)
        if self.world.w != cfg.world_width:
            self.log.info("world width rounded up to %d (a multiple of 64)", self.world.w)
        self._config_rule = self.world.rule        # the rule config.json asked for
        self._config_size = (self.world.w, self.world.h)
        self.world_name = ""                       # the saved world this one came from, if any
        camera_state = (persistence.load_world(self.world, log=self.log.warning,
                                               config_size=self._config_size)
                        if cfg.restore_world else None)
        if camera_state is not None:
            # The saved world brings back the rule it was running -- unless
            # config.json was edited since, in which case the file wins.
            saved_config_rule = camera_state.pop("config_rule", None)
            if saved_config_rule is not None and saved_config_rule != self._config_rule:
                self.world.set_rule(self._config_rule)
            cfg.rule = self.world.rule
            camera_state.pop("config_size", None)
            self.world_name = str(camera_state.pop("world_name", "") or "")
            gps = camera_state.pop("gps", None)
            if self.world_name and gps is not None:       # a saved world runs at its own speed
                self.gps = _number(gps, float, self.gps, 0.1, 240.0)
            if (self.world.w, self.world.h) != self._config_size:
                self.log.info("restored a %dx%d world (%s)", self.world.w, self.world.h,
                              self.world_name or "opened from a file")
        if camera_state is None:
            self.world.seed_soup(cfg.density)
            started = time.perf_counter()
            ran = 0
            # Bounded by time as well: a huge world must not keep the tray icon
            # and the wallpaper waiting for a warm-up nobody asked to watch.
            while ran < max(0, int(cfg.warmup_generations)) and time.perf_counter() - started < 3.0:
                self.world.step()
                ran += 1
            self.log.info("seeded a fresh world and ran %d generations in %.2fs",
                          ran, time.perf_counter() - started)
        else:
            self.log.info("restored the world at generation %s (%s cells alive)",
                          f"{self.world.generation:,}", f"{self.world.population:,}")
        names = palettes.names()
        self._palette_index = names.index(cfg.palette) if cfg.palette in names else 0
        self.look = Look(palettes.get(names[self._palette_index]), cfg.age_span, cfg.trail_seconds)
        self.grid = Grid(cfg.grid_levels, cfg.grid_strength, cfg.grid_opacity)
        self.grid.enabled = bool(cfg.grid)

        # -- the view ---------------------------------------------------------
        self.screen = w.virtual_screen()
        self.camera = Camera(self.screen[2], self.screen[3], self.world.w, self.world.h, cfg.zoom or 8)
        if cfg.zoom <= 0:
            self.camera.fit()
            self.camera.center()
        if camera_state:
            self.camera.restore(camera_state)
        self._dpi_scale = self._system_dpi() / 96.0

        # -- capabilities -----------------------------------------------------
        self.policy = PowerPolicy(cfg.pause_when_busy, OUR_CLASSES)
        self._verdict = Verdict(True, "")
        self.occlusion = Occlusion(set())
        self._visible_monitors: list[bool] = []
        self.system = SystemState()
        self.system.refresh_static()
        self.probe = host.VisibilityProbe()
        self.editor = Editor(self)
        self.panel = PanelHost(self)
        self.mouse = DesktopMouse(self._is_desktop_root, self._wake)
        self.tray = None
        self.renderer: Renderer | None = None
        self.layer: host.DesktopLayer | None = None
        self.surfaces: list = []                 # (Window, Surface, monitor)
        self.window: Window | None = None        # windowed mode only
        self._forced_layer: str | None = None
        self._layers_tried: set[str] = set()
        self._monitor_signature = self._monitors_now()

        # -- timing -----------------------------------------------------------
        now = time.perf_counter()
        self._last_tick = now
        self._last_render = now
        self._next_render = now
        self._next_watch = now + 1.0
        self._next_policy = now
        self._next_occlusion = now
        self._last_occlusion = 0.0
        self._occlusion_dirty = True
        self._next_autosave = now + 600.0
        self._next_status = now + 2.0
        self._next_display_check = now + 2.0
        self._display_dirty_at = 0.0
        self._reattach_at = 0.0
        self._reattach_tries = 0
        self._probe_due = 0.0
        self._probe_tries = 0
        self._health_at = 0.0
        self._palette_due = time.monotonic() + cfg.cycle_minutes * 60
        self._trail_until = now + 3.0
        self._rendered = None
        self._dirty = True
        self._frames_counted = 0
        self._fps_since = now
        self._render_errors: deque = deque(maxlen=8)
        self._last_cursor = None
        self._hook_probe_pos = None
        self._loop_errors: list[float] = []
        self.panel_closed = True
        self._modal = False
        self._power_notes: list = []
        self._win_event_hooks: list = []
        self._win_event_proc = w.WINEVENTPROC(self._on_win_event)

        # Messages arrive from inside CreateWindowEx, so everything the handler
        # reads must exist before the window does.
        self._taskbar_created = w.RegisterWindowMessageW("TaskbarCreated")
        self.control = None
        self.control = Window(CONTROL_CLASS, "Game of Life control", w.WS_POPUP, w.WS_EX_TOOLWINDOW,
                              (0, 0, 0, 0), self._on_control)
        self.waiter = w.Waiter()

    # ================================================================ threads ===
    def call(self, fn, *args) -> None:
        """Run ``fn(*args)`` on the main thread, soon.  Safe from any thread."""
        self._commands.append((fn, args))
        self._wake()

    def _wake(self) -> None:
        hwnd = self.control.hwnd if self.control is not None else None
        if hwnd:
            w.PostMessageW(hwnd, WM_WAKE, 0, 0)

    def _run_commands(self) -> None:
        for _ in range(len(self._commands)):
            try:
                fn, args = self._commands.popleft()
            except IndexError:
                break
            try:
                fn(*args)
            except Exception:
                self.log_exception(f"command {getattr(fn, '__name__', fn)}")
            self._dirty = True

    def log_exception(self, what: str) -> None:
        self.log.exception("%s failed", what)

    def say(self, text: str) -> None:
        self.message = text
        self.message_serial += 1

    # ================================================================ desktop ===
    @staticmethod
    def _system_dpi() -> int:
        try:
            fn = ctypes.windll.user32.GetDpiForSystem
            fn.restype = wintypes.UINT
            return int(fn()) or 96
        except (AttributeError, OSError):
            return 96

    @staticmethod
    def _monitors_now():
        return (tuple(w.monitors()), w.virtual_screen())

    def _is_desktop_root(self, hwnd: int) -> bool:
        if not hwnd:
            return False
        if any(int(win.hwnd or 0) == int(hwnd) for win, _, _ in self.surfaces):
            return True
        return w.class_name(hwnd) in host.DESKTOP_ROOTS

    def _build_renderer(self) -> bool:
        try:
            self.renderer = Renderer(self.world.w, self.world.h)
            self.renderer.request_reset(age=min(255.0, self.cfg.age_span * 2.0))
            self.log.info("renderer: Direct3D 11 on %s, feature level %X", self.renderer.driver,
                          self.renderer.device.feature_level)
            return True
        except Exception:
            self.log_exception("creating the renderer")
            self.renderer = None
            return False

    def _attach(self, allow_partial: bool = False) -> bool:
        """Put surfaces on the desktop; False when there is no desktop to use yet."""
        if self.renderer is None and not self._build_renderer():
            return False
        try:
            return self._attach_surfaces(allow_partial)
        except Exception:
            self.log_exception("attaching to the desktop")
            return False
        finally:
            # A device that died while nothing was being presented only shows
            # itself here, as a swap chain that cannot be created.
            if not self.surfaces and self.renderer is not None and not self.renderer.healthy():
                self.log.warning("the graphics device is gone; it will be recreated")
                try:
                    self.renderer.release()
                except Exception:
                    pass
                self.renderer = None

    def _attach_surfaces(self, allow_partial: bool) -> bool:
        if self.windowed:
            return self._attach_window()
        mode = self.cfg.attach_mode
        if mode == "auto":
            mode = self._forced_layer or persistence.learned_layer() or "auto"
        layer = host.find_layer(mode, allow_partial)
        if layer is None and mode != "auto":
            layer = host.find_layer("auto", allow_partial)
        if layer is None:
            return False
        monitors = w.monitors() or [(*w.virtual_screen(), True)]
        vx, vy, vw, vh = w.virtual_screen()
        self.screen = (vx, vy, vw, vh)
        self.camera.set_screen(vw, vh)
        created = []
        layered = True
        while True:
            try:
                for monitor in monitors:
                    win = host.create_surface(layer, monitor, self._on_surface, layered=layered)
                    try:
                        surface = self.renderer.add_surface(win.hwnd, monitor[:4],
                                                            (monitor[0] - vx, monitor[1] - vy))
                    except Exception:
                        win.destroy()
                        raise
                    created.append((win, surface, monitor))
                break
            except Exception:
                for win, surface, _ in created:
                    self.renderer.remove_surface(surface)
                    win.destroy()
                created = []
                if layer.kind == "bottom" and layered:
                    layered = False                   # retry without click-through
                    continue
                self.log_exception(f"creating surfaces as {layer.kind}")
                return False
        self.surfaces, self.layer = created, layer
        self._layers_tried.add(layer.kind)
        self.occlusion.own = {int(win.hwnd) for win, _, _ in created}
        self._monitor_signature = self._monitors_now()
        # Draw the first frame while the windows are still hidden, so nothing
        # ever flashes black over the desktop.
        self._render(time.perf_counter(), force=True)
        for win, surface, monitor in created:
            surface.shown = self.wallpaper_visible
            host.place(layer, win.hwnd, monitor, show=self.wallpaper_visible)
        self.log.info("presenting as %s on %d monitor(s): %s", layer.describe(), len(created),
                      w.describe_monitors())
        if layer.kind == "bottom":
            self.log.warning("the bottom layer covers the desktop icons; clicks still reach them")
        rivals = host.competing_wallpapers(layer, self.occlusion.own)
        if rivals:
            self.log.warning("another wallpaper program is on the desktop too: %s", ", ".join(rivals))
        if host.reveal_wallpaper(layer):
            self.log.info("showed the WorkerW that paints the Windows wallpaper (it was hidden)")
        self._probe_due = time.perf_counter() + 0.8
        self._probe_tries = 0
        self._dirty = True
        return True

    def _attach_window(self) -> bool:
        if self.window is None:
            vx, vy, vw, vh = w.virtual_screen()
            self.window = Window(WINDOW_CLASS, t("app"), w.WS_OVERLAPPEDWINDOW, w.WS_EX_NOREDIRECTIONBITMAP,
                                 (80, 80, min(1600, vw - 160), min(900, vh - 160)), self._on_window,
                                 class_style=w.CS_DBLCLKS)
            w.set_dark_title_bar(self.window.hwnd)
            w.ShowWindow(self.window.hwnd, w.SW_SHOW)
        rect = wintypes.RECT()
        w.GetClientRect(self.window.hwnd, ctypes.byref(rect))
        width, height = max(1, rect.right), max(1, rect.bottom)
        self.screen = (0, 0, width, height)
        self.camera.set_screen(width, height)
        surface = self.renderer.add_surface(self.window.hwnd, (0, 0, width, height), (0, 0))
        self.surfaces = [(self.window, surface, (0, 0, width, height, True))]
        self.layer = host.DesktopLayer("window", 0)
        self._render(time.perf_counter(), force=True)
        return True

    def _detach(self) -> None:
        for win, surface, _ in self.surfaces:
            if self.renderer is not None:
                try:
                    self.renderer.remove_surface(surface)
                except Exception:
                    self.log_exception("releasing a surface")
            if win is not self.window:
                win.destroy()
        self.surfaces = []
        # Whatever Explorer did meanwhile, leave the Windows wallpaper on show.
        host.reveal_wallpaper(self.layer)
        self.layer = None
        self.occlusion.own = set()

    def _schedule_reattach(self, delay: float = 0.5) -> None:
        if not self._reattach_at:
            self._reattach_at = time.perf_counter() + delay

    def _try_reattach(self, now: float) -> None:
        self._reattach_at = 0.0
        # After a while, settle for a desktop without its icon view: better a
        # wallpaper above the Windows one than none at all.
        if self._attach(allow_partial=self._reattach_tries >= 10):
            if self._reattach_tries:
                self.log.info("re-attached to the desktop")
            self._reattach_tries = 0
            return
        self._reattach_tries += 1
        if self._reattach_tries in (1, 5, 20) or self._reattach_tries % 60 == 0:
            self.log.warning("no desktop to attach to yet (attempt %d); will keep trying",
                             self._reattach_tries)
        if self._reattach_tries == 20 and self.tray is not None:
            self.tray.notify(t("balloon.noattach.title"), t("balloon.noattach.body"), warning=True)
        self._reattach_at = now + min(5.0, 0.5 * (1.5 ** min(self._reattach_tries, 8)))

    def _rebuild(self, why: str) -> None:
        self.log.info("rebuilding the wallpaper: %s", why)
        self._detach()
        if self.windowed:
            self._attach()
            return
        self._reattach_at = 0.0
        self._try_reattach(time.perf_counter())

    def _device_lost(self, why: str) -> None:
        self.log.warning("the graphics device was lost (%s); starting over", why)
        self._detach()
        if self.renderer is not None:
            try:
                self.renderer.release()
            except Exception:
                pass
            self.renderer = None
        self._reattach_at = time.perf_counter() + 1.0

    # ================================================================ messages ==
    def _on_control(self, hwnd, msg, wparam, lparam):
        if msg == WM_WAKE:
            return 0
        if msg == w.WM_HOTKEY and wparam == HOTKEY_ID:
            self.toggle_interactive()
            return 0
        if msg == WM_TRAY:
            if self.tray is not None:
                self.tray.on_callback(wparam, lparam)
            return 0
        if msg == WM_OPEN_EDITOR:
            self.set_interactive(True)
            return 0
        if msg == w.WM_TIMER and wparam == MODAL_TIMER:
            if self._modal:                       # the tray menu's loop is running, not ours
                self._run_commands()
                now = time.perf_counter()
                self._guarded("housekeeping", self._housekeeping, now)
                self._guarded("a frame", self._frame, now)
            return 0
        if msg == self._taskbar_created:
            self.log.info("the taskbar was recreated (Explorer restarted)")
            if self.tray is not None:
                self.tray.add()
            self._next_watch = 0.0
            return 0
        if msg in (w.WM_DISPLAYCHANGE, w.WM_DPICHANGED) or (
                msg == w.WM_SETTINGCHANGE and wparam == w.SPI_SETWORKAREA):
            self._display_dirty_at = time.perf_counter() + 0.5
            return 0
        if msg == w.WM_POWERBROADCAST:
            self._on_power(wparam, lparam)
            return 1
        if msg == w.WM_WTSSESSION_CHANGE:
            change = self.system.on_session(int(wparam))
            if change:
                self.log.info("%s", change)
            if wparam == w.WTS_SESSION_LOCK:
                # A press made before the lock screen will never see its release.
                self.mouse.filter.cancel()
                self.mouse.events.clear()
                self.editor.cancel()
            return 0
        if msg == w.WM_QUERYENDSESSION:
            return 1
        if msg == w.WM_ENDSESSION:
            if wparam:
                self.log.info("Windows is shutting down")
                self._save_world()
                self.running = False
            return 0
        if msg == w.WM_CLOSE:
            self.stop()
            return 0
        return None

    def _on_power(self, wparam: int, lparam: int) -> None:
        if wparam == w.PBT_POWERSETTINGCHANGE and lparam:
            change = self.system.on_power_setting(lparam)
            if change:
                self.log.info("%s", change)
        elif wparam == w.PBT_APMSUSPEND:
            self.log.info("going to sleep")
            self.system.asleep = True
            self._save_world()
        elif wparam in (w.PBT_APMRESUMEAUTOMATIC, w.PBT_APMRESUMESUSPEND):
            if self.system.asleep:
                self.log.info("woke up")
            self.system.asleep = False
            self._health_at = time.perf_counter() + 2.0
            self._display_dirty_at = time.perf_counter() + 1.0

    def _on_surface(self, hwnd, msg, wparam, lparam):
        if msg == w.WM_NCHITTEST:
            return w.HTTRANSPARENT
        if msg == w.WM_MOUSEACTIVATE:
            return w.MA_NOACTIVATE
        if msg == w.WM_ERASEBKGND:
            return 1
        if msg == w.WM_PAINT:
            ps = w.PAINTSTRUCT()
            w.BeginPaint(hwnd, ctypes.byref(ps))
            w.EndPaint(hwnd, ctypes.byref(ps))
            return 0
        if msg == w.WM_WINDOWPOSCHANGING and self.layer is not None and self.layer.kind == "bottom":
            pos = ctypes.cast(lparam, ctypes.POINTER(w.WINDOWPOS)).contents
            if not pos.flags & w.SWP_NOZORDER:
                pos.hwndInsertAfter = w.HWND_BOTTOM
            return None
        if msg == w.WM_NCDESTROY:
            self._next_watch = 0.0                # destroyed from outside: look now
        return None

    _MOUSE_DOWN = {w.WM_LBUTTONDOWN: LEFT, w.WM_RBUTTONDOWN: RIGHT, w.WM_MBUTTONDOWN: MIDDLE,
                   w.WM_LBUTTONDBLCLK: LEFT}
    _MOUSE_UP = {w.WM_LBUTTONUP: LEFT, w.WM_RBUTTONUP: RIGHT, w.WM_MBUTTONUP: MIDDLE}

    def _on_window(self, hwnd, msg, wparam, lparam):
        """The ordinary window of ``--windowed`` mode: input comes straight in."""
        if msg == w.WM_CLOSE:
            self.stop()
            return 0
        if msg == w.WM_SIZE:
            self._display_dirty_at = time.perf_counter() + 0.05
            return 0
        if msg == w.WM_ERASEBKGND:
            return 1
        if msg == w.WM_PAINT:
            ps = w.PAINTSTRUCT()
            w.BeginPaint(hwnd, ctypes.byref(ps))
            w.EndPaint(hwnd, ctypes.byref(ps))
            self._dirty = True
            return 0
        if not self.interactive:
            if msg == w.WM_LBUTTONDBLCLK:
                self.set_interactive(True)
                return 0
            return None
        x, y = w.signed_lo_hi(lparam)
        if msg in self._MOUSE_DOWN:
            ctypes.windll.user32.SetCapture(wintypes.HWND(hwnd))
            self.editor.press(self._MOUSE_DOWN[msg], x, y)
            return 0
        if msg in self._MOUSE_UP:
            self.editor.release(self._MOUSE_UP[msg], x, y)
            if not self.editor._buttons:
                ctypes.windll.user32.ReleaseCapture()
            return 0
        if msg == w.WM_MOUSEMOVE:
            if self.editor._drag is not None:
                self.editor.move(x, y)
            else:
                self.editor.hover((x, y))
            return 0
        if msg == w.WM_MOUSEWHEEL:
            pt = wintypes.POINT(*w.signed_lo_hi(lparam))
            ctypes.windll.user32.ScreenToClient(wintypes.HWND(hwnd), ctypes.byref(pt))
            delta = ctypes.c_short((wparam >> 16) & 0xFFFF).value
            self.editor.wheel(delta, pt.x, pt.y)
            return 0
        if msg == w.WM_KEYDOWN:
            if wparam == 0x1B:
                self.set_interactive(False)
            elif wparam == 0x20:
                self.toggle_pause()
            return 0
        return None

    def _on_win_event(self, hook, event, hwnd, id_object, id_child, thread, time_ms):
        if id_object == w.OBJID_WINDOW:
            self._occlusion_dirty = True

    # ================================================================ setup =====
    def _register_hotkey(self) -> bool:
        w.UnregisterHotKey(self.control.hwnd, HOTKEY_ID)
        for spec in self.cfg.hotkey_candidates():
            try:
                mods, vk = parse_hotkey(spec)
            except ValueError as exc:
                self.log.warning("bad hotkey %r: %s", spec, exc)
                continue
            if w.RegisterHotKey(self.control.hwnd, HOTKEY_ID, mods, vk):
                self.hotkey_spec = spec
                self.log.info("editor hotkey: %s", spec)
                return True
            self.log.info("hotkey %s is already taken by another program", spec)
        self.hotkey_spec = ""
        self.log.warning("no editor hotkey available -- use the tray icon instead")
        return False

    def _register_notifications(self) -> None:
        hwnd = self.control.hwnd
        for guid in (w.GUID_CONSOLE_DISPLAY_STATE, w.GUID_ACDC_POWER_SOURCE, w.GUID_POWER_SAVING_STATUS):
            handle = w.RegisterPowerSettingNotification(hwnd, ctypes.byref(guid), w.DEVICE_NOTIFY_WINDOW_HANDLE)
            if handle:
                self._power_notes.append(handle)
        if w.WTSRegisterSessionNotification is not None:
            w.WTSRegisterSessionNotification(hwnd, w.NOTIFY_FOR_THIS_SESSION)
        flags = w.WINEVENT_OUTOFCONTEXT | w.WINEVENT_SKIPOWNPROCESS
        for low, high in ((w.EVENT_SYSTEM_FOREGROUND, w.EVENT_SYSTEM_FOREGROUND),
                          (w.EVENT_SYSTEM_MOVESIZEEND, w.EVENT_SYSTEM_MOVESIZEEND),
                          (w.EVENT_SYSTEM_MINIMIZESTART, w.EVENT_SYSTEM_MINIMIZEEND),
                          (w.EVENT_OBJECT_CLOAKED, w.EVENT_OBJECT_UNCLOAKED)):
            handle = w.SetWinEventHook(low, high, None, self._win_event_proc, 0, 0, flags)
            if handle:
                self._win_event_hooks.append(handle)

    def _unregister_notifications(self) -> None:
        for handle in self._power_notes:
            w.UnregisterPowerSettingNotification(handle)
        self._power_notes.clear()
        if w.WTSUnRegisterSessionNotification is not None and self.control.hwnd:
            w.WTSUnRegisterSessionNotification(self.control.hwnd)
        for handle in self._win_event_hooks:
            w.UnhookWinEvent(handle)
        self._win_event_hooks.clear()

    def _first_run_hint(self) -> None:
        state = persistence.load_state()
        if state.get("hint_shown") or self.tray is None:
            return
        body = t("balloon.body", hotkey=self.hotkey_spec) if self.hotkey_spec else t("balloon.body.nohotkey")
        self.tray.notify(t("balloon.title"), body)
        persistence.save_state(hint_shown=True)

    # ================================================================ the loop ==
    def run(self) -> None:
        self.running = True
        if self.cfg.tray_icon:
            from .ui.tray import Tray
            self.tray = Tray(self)
        self._register_hotkey()
        self._register_notifications()
        self._try_reattach(time.perf_counter())
        self._first_run_hint()
        msg = self._msg
        try:
            while self.running:
                while w.PeekMessageW(ctypes.byref(msg), None, 0, 0, w.PM_REMOVE):
                    if msg.message == w.WM_QUIT:
                        self.running = False
                        break
                    w.TranslateMessage(ctypes.byref(msg))
                    w.DispatchMessageW(ctypes.byref(msg))
                if not self.running:
                    break
                self._run_commands()
                now = time.perf_counter()
                self._guarded("housekeeping", self._housekeeping, now)
                self._guarded("a frame", self._frame, now)
                if self.running:
                    self.waiter.wait(self._next_wait(time.perf_counter()))
        finally:
            self.close()

    def _guarded(self, what: str, step, *args) -> None:
        """Run one stage of the loop; an exception is logged, never fatal.

        A wallpaper that quits over one bad frame is worse than one that skips
        it.  Logging is throttled so a persistent fault cannot flood the log.
        """
        try:
            step(*args)
        except Exception:
            now = time.monotonic()
            self._loop_errors = [t for t in self._loop_errors if now - t < 60.0] + [now]
            if len(self._loop_errors) <= 3:
                self.log_exception(what)
            elif len(self._loop_errors) == 4:
                self.log.error("more errors in the main loop; logging at most three a minute")

    def stop(self) -> None:
        """Ask the loop to finish.  Safe from any thread."""
        self.running = False
        self._wake()

    # -- per-iteration work -------------------------------------------------------
    def _rest(self) -> str:
        s, cfg = self.system, self.cfg
        if s.asleep:
            return "asleep"
        if s.locked:
            return "session locked"
        if s.display_off:
            return "display off"
        if cfg.pause_in_remote_session and s.remote:
            return "remote session"
        if cfg.pause_on_battery_saver and s.saver:
            return "battery saver"
        if not self._verdict:
            return self._verdict.reason
        if not self.wallpaper_visible:
            return "hidden"
        if cfg.pause_when_hidden and self._visible_monitors and not any(self._visible_monitors):
            return "desktop covered"
        return ""

    def _frame(self, now: float) -> None:
        dt = min(0.25, max(0.0, now - self._last_tick))
        self._last_tick = now
        reason = self._rest()
        # With the editor open, only rest when nobody can be using it: the hook
        # keeps capturing desktop clicks, and they must keep doing something.
        if self.interactive and reason not in ("asleep", "session locked", "display off"):
            reason = ""
        if reason != self.rest_reason:
            if reason:
                self.log.info("resting: %s", reason)
            else:
                self.log.info("resuming (was resting: %s)", self.rest_reason)
                self.mouse.events.clear()             # nothing stale replays as a stroke
            self.rest_reason = reason
            if self.tray is not None:
                self.tray.update()
        if reason:
            return
        if self.interactive:
            self._drain_input()
        self._simulate(dt, now)
        if self.look.advance(dt):
            self._dirty = True
        if self.cfg.cycle_palettes and time.monotonic() >= self._palette_due:
            self.next_palette()
        if now >= self._next_render and self._needs_render(now):
            self._render(now)

    def _advance(self, now: float) -> None:
        stats = self.world.step()
        if stats.died and self.look.trail_seconds > 0:
            self._trail_until = now + self.look.trail_seconds * 1.25

    def _simulate(self, dt: float, now: float) -> None:
        if self.skip_remaining:
            start = time.perf_counter()
            deadline, done = start + 0.025, 0
            while self.skip_remaining and time.perf_counter() < deadline:
                self._advance(now)
                self.skip_remaining -= 1
                done += 1
            elapsed = time.perf_counter() - start
            if done and elapsed > 0:
                rate = done / elapsed
                self.skip_rate = rate if not self.skip_rate else self.skip_rate * 0.8 + rate * 0.2
            if not self.skip_remaining:
                self.renderer_reset(mature=True)
            return
        if self.paused or self.gps <= 0:
            self.budget = 0.0
            return
        self.budget += dt * self.gps
        steps = int(self.budget)
        if steps:
            self.budget -= steps
            # Bounded by count and by time: a huge world at a high speed must
            # still leave the loop free to answer messages every few ms.
            deadline = time.perf_counter() + 0.020
            for _ in range(min(steps, 16)):
                self._advance(now)
                if time.perf_counter() > deadline:
                    break
            if steps > 16 or time.perf_counter() > deadline:
                self.budget = 0.0                     # never spiral after a stall

    def _drain_input(self) -> None:
        events = self.mouse.events
        vx, vy = self.screen[:2]
        editor = self.editor
        moves: list[tuple[int, int]] = []       # consecutive moves go to the editor as one path
        while events:
            try:
                kind, x, y, data = events.popleft()
            except IndexError:
                break
            sx, sy = x - vx, y - vy
            if kind == MOVE:
                moves.append((sx, sy))
                continue
            if moves:
                editor.move_path(moves)
                moves = []
            if kind == DOWN:
                editor.press(data, sx, sy)
            elif kind == UP:
                editor.release(data, sx, sy)
            elif kind == WHEEL:
                editor.wheel(data, sx, sy)
        if moves:
            editor.move_path(moves)
        if self.windowed:
            return
        pos = w.cursor_pos()
        if pos is not None and pos != self._last_cursor:
            self._last_cursor = pos
            over = self._is_desktop_root(w.root_at(*pos))
            editor.hover((pos[0] - vx, pos[1] - vy) if over else None)

    def _target_fps(self) -> float:
        fps = float(self.cfg.fps)
        if self.interactive:
            return max(60.0, fps)
        if self.system.on_battery:
            fps = min(fps, float(self.cfg.battery_fps))
        return max(1.0, fps)

    def _versions(self):
        return (self.world.version, self.look.version, self.camera.version,
                self.editor.version if self.interactive else -1, self.interactive)

    def _needs_render(self, now: float) -> bool:
        if self.skip_remaining and now - self._last_render < 0.1:
            return False                          # let a skip have the time
        return (self._dirty or now < self._trail_until or self.look.fading
                or self._versions() != self._rendered)

    def _frame_spec(self) -> FrameSpec:
        vp = self.camera.viewport()
        grid = ()
        if self.grid.enabled:
            # vp.scale: pixels per cell, fractional when zoomed out (vp.zoom is then 1).
            grid = tuple((step, self.grid.opacity_for(step, vp.scale)) for step in self.grid.levels
                         if self.grid.opacity_for(step, vp.scale) > 0)[:4]
        spec = FrameSpec(viewport=vp, grid=grid, smooth=bool(self.cfg.smooth))
        if self.interactive:
            boxes, mask, rect, colour = self.editor.overlay()
            spec.boxes = boxes
            if mask is not None and self.renderer is not None:
                self.renderer.set_mask(mask, self.editor.mask_version)
                spec.mask_rect, spec.mask_colour = rect, colour
            if self.cfg.editor_frame and not self.windowed:
                r, g, b = self.look.accent()
                spec.frame_colour = (r / 255, g / 255, b / 255, 0.85)
                spec.frame_width = max(2, round(3 * self._dpi_scale))
        return spec

    def _render(self, now: float, force: bool = False, spec: FrameSpec | None = None) -> None:
        if self.renderer is None or not self.surfaces:
            return
        surfaces = []
        for index, (win, surface, _) in enumerate(self.surfaces):
            if not surface.shown and not force:
                continue
            if (not force and self._visible_monitors
                    and index < len(self._visible_monitors) and not self._visible_monitors[index]
                    and self.layer is not None and self.layer.kind != "window"):
                continue                          # this monitor's desktop is covered
            surfaces.append(surface)
        dt = max(0.0, now - self._last_render)
        try:
            self.renderer.render(self.world, self.look, spec or self._frame_spec(), dt, surfaces)
        except DeviceLost as exc:
            self._device_lost(str(exc))
            return
        except Exception:
            self._render_errors.append(now)
            if len(self._render_errors) == 1 or len(self._render_errors) == self._render_errors.maxlen:
                self.log_exception("rendering a frame")
            if len(self._render_errors) >= 5 and now - self._render_errors[-5] < 10.0:
                self._render_errors.clear()
                self._device_lost("repeated rendering errors")
            return
        self._last_render = now
        interval = 1.0 / self._target_fps()
        self._next_render = max(now + interval * 0.5, self._next_render + interval)
        self._rendered = self._versions()
        self._dirty = False
        self._frames_counted += 1
        if now - self._fps_since >= 1.0:
            self.fps = self._frames_counted / (now - self._fps_since)
            self._frames_counted = 0
            self._fps_since = now

    def _next_wait(self, now: float) -> float:
        deadlines = [self._next_watch, self._next_policy, self._next_occlusion, self._next_status,
                     self._next_display_check]
        for extra in (self._reattach_at, self._probe_due, self._display_dirty_at, self._health_at):
            if extra:
                deadlines.append(extra)
        if self._occlusion_dirty and not self.windowed:
            deadlines.append(self._last_occlusion + 0.15)
        if not self.rest_reason:
            if self.skip_remaining or self._commands:
                return 0.0
            if not self.paused and self.gps > 0:
                deadlines.append(now + max(0.0, (1.0 - self.budget) / self.gps))
            if self._needs_render(now):
                deadlines.append(self._next_render)
            if self.look.fading:
                deadlines.append(now + 1.0 / self._target_fps())
            if self.interactive:
                deadlines.append(now + 1.0 / 60.0)
        return max(0.0, min(1.0, min(deadlines) - now))

    # -- housekeeping ---------------------------------------------------------------
    def _housekeeping(self, now: float) -> None:
        if self._reattach_at and now >= self._reattach_at:
            self._try_reattach(now)
        if now >= self._next_watch:
            self._next_watch = now + 1.0
            self._watch(now)
        if self._display_dirty_at and now >= self._display_dirty_at:
            self._display_dirty_at = 0.0
            self._display_changed(now)
        if now >= self._next_display_check:
            self._next_display_check = now + 2.0
            if not self.windowed and self._monitors_now() != self._monitor_signature and self.surfaces:
                self._display_changed(now)
        if self._health_at and now >= self._health_at:
            self._health_at = 0.0
            if self.renderer is not None and not self.renderer.healthy():
                self._device_lost("after resuming")
        if self._probe_due and now >= self._probe_due:
            self._probe_due = 0.0
            self._run_probe(now)
        if now >= self._next_policy:
            self._next_policy = now + 1.0
            verdict = self.policy.check()
            if bool(verdict) != bool(self._verdict):
                self.log.info("full-screen check: %s", verdict.reason)
            self._verdict = verdict
            if self.system.remote != bool(w.GetSystemMetrics(w.SM_REMOTESESSION)):
                self.system.remote = not self.system.remote
        # Window events mark occlusion dirty, sometimes dozens of times a second
        # (a window being dragged); look at most every 150 ms, and every second anyway.
        if not self.windowed and (now >= self._next_occlusion or (
                self._occlusion_dirty and now - self._last_occlusion >= 0.15)):
            self._occlusion_dirty = False
            self._last_occlusion = now
            self._next_occlusion = now + 1.0
            if self.surfaces:
                rects = [(m[0], m[1], m[0] + m[2], m[1] + m[3]) for _, _, m in self.surfaces]
                try:
                    visible = self.occlusion.visible(rects)
                except Exception:
                    visible = [True] * len(rects)
                if visible != self._visible_monitors:
                    newly = [i for i, v in enumerate(visible)
                             if v and (i >= len(self._visible_monitors) or not self._visible_monitors[i])]
                    self._visible_monitors = visible
                    if newly:
                        self._dirty = True        # repaint what was uncovered
        if now >= self._next_status:
            self._next_status = now + 2.0
            if self.tray is not None:
                self.tray.update()
            if self.interactive and not self.windowed:
                pos = w.cursor_pos()
                if not self.mouse.active:
                    self.log.warning("the mouse hook stopped; restarting it")
                    self.editor.cancel()
                    self.mouse.restart()
                elif (self._hook_probe_pos is not None and pos != self._hook_probe_pos
                      and time.monotonic() - self.mouse.last_event > 2.5):
                    # The pointer moved but the hook heard nothing: Windows
                    # drops a hook it considers too slow, without telling us.
                    self.log.warning("the mouse hook went quiet; reinstalling it")
                    self.editor.cancel()
                    self.mouse.restart()
                self._hook_probe_pos = pos
        if now >= self._next_autosave:
            self._next_autosave = now + 600.0
            self._save_world()

    def _watch(self, now: float) -> None:
        """Keep the surfaces alive and in their slot under the icons."""
        if self.windowed or self._reattach_at:
            return
        if not self.surfaces or self.layer is None:
            self._schedule_reattach(0.5)
            return
        fresh = host.refresh_layer(self.layer)
        if fresh != self.layer:
            self.log.info("Explorer recreated the icon view; following it")
            self.layer = fresh
        if not self.layer.alive() or any(not win.exists() for win, _, _ in self.surfaces):
            self.log.warning("the desktop went away (Explorer restarted?); re-attaching")
            self._detach()
            self._schedule_reattach(0.5)
            return
        for win, _, _ in self.surfaces:
            if host.restack(self.layer, win.hwnd):
                self.log.info("Explorer reordered the desktop; put the wallpaper back under the icons")

    def _display_changed(self, now: float) -> None:
        if self.windowed:
            if self.window is None or not self.surfaces:
                return
            rect = wintypes.RECT()
            w.GetClientRect(self.window.hwnd, ctypes.byref(rect))
            width, height = max(1, rect.right), max(1, rect.bottom)
            surface = self.surfaces[0][1]
            if (width, height) != surface.size:
                try:
                    surface.chain.resize(width, height)
                    surface.rect = (0, 0, width, height)
                    self.surfaces[0] = (self.window, surface, (0, 0, width, height, True))
                    self.screen = (0, 0, width, height)
                    self.camera.set_screen(width, height)
                    self._dirty = True
                except Exception:
                    self.log_exception("resizing the window")
            return
        signature = self._monitors_now()
        if signature != self._monitor_signature or not self.surfaces:
            self._rebuild(f"displays changed: {w.describe_monitors()}")

    def _run_probe(self, now: float) -> None:
        if not self.surfaces or self.layer is None or self.layer.kind == "window" or self.renderer is None:
            return
        points = self.probe.sample_points([s.rect for _, s, _ in self.surfaces], self.occlusion.own)

        def show(colour):
            spec = self._frame_spec()
            spec.probe_colour = None if colour is None else tuple(c / 255 for c in colour)
            self.renderer.render(self.world, self.look, spec, 0.0,
                                 [s for _, s, _ in self.surfaces if s.shown])

        try:
            result = self.probe.run(points, show, host.settle_composition)
        except DeviceLost as exc:
            self._device_lost(str(exc))
            return
        except Exception:
            self.log_exception("checking that the wallpaper is visible")
            return
        if result is None:
            self._probe_tries += 1
            if self._probe_tries == 1:
                self.log.info("cannot check that the wallpaper is visible yet: %s", self.probe.describe())
            if self._probe_tries < 20:
                self._probe_due = now + 15.0      # every monitor is covered; look again later
            return
        if result:
            self.log.info("the wallpaper is visible on screen (%s)", self.probe.describe())
            if self.cfg.attach_mode == "auto":
                persistence.remember_layer(self.layer.kind)
            return
        self.log.warning("the wallpaper is NOT reaching the screen as %s (%s)",
                         self.layer.kind, self.probe.describe())
        other = OTHER_LAYOUT.get(self.layer.kind)
        if self.cfg.attach_mode == "auto" and other and other not in self._layers_tried:
            if host.find_layer(other) is not None:
                self._forced_layer = other
                self._rebuild(f"trying the {other} layout instead")
                return
        # No balloon here: a colour-managed or HDR screen can fool the probe,
        # and the layout it found is still the right one to be in.

    # ================================================================ commands ==
    # Everything below runs on the main thread (the panel reaches it via call()).
    def toggle_interactive(self) -> None:
        self.set_interactive(not self.interactive)

    def set_interactive(self, on: bool) -> None:
        on = bool(on)
        if on == self.interactive:
            if on:
                self.panel.show()
            return
        if on:
            if not self.windowed and not self.mouse.start():
                self.log.warning("could not install the mouse hook; drawing on the desktop will not work")
            self.interactive = True
            self._last_cursor = None
            self.panel.show()
            self.log.info("editor: open")
        else:
            self.mouse.stop()
            self.interactive = False
            self.editor.cancel()
            self.panel.hide()
            self.log.info("editor: closed")
        self._dirty = True
        self._next_render = time.perf_counter()
        if self.tray is not None:
            self.tray.update()

    def editor_call(self, method: str, *args) -> None:
        getattr(self.editor, method)(*args)

    def begin_modal(self) -> None:
        """A modal loop (the tray menu) is about to take over: keep frames coming."""
        self._modal = True
        # WM_TIMER rounds up to the 15.6 ms system tick; ask for one tick.
        w.SetTimer(self.control.hwnd, MODAL_TIMER, 15, None)

    def end_modal(self) -> None:
        w.KillTimer(self.control.hwnd, MODAL_TIMER)
        self._modal = False

    def toggle_pause(self) -> bool:
        self.paused = not self.paused
        self.budget = 0.0
        return self.paused

    def set_paused(self, paused: bool) -> None:
        self.paused = bool(paused)

    def step_once(self) -> None:
        self.paused = True
        self._advance(time.perf_counter())

    def set_speed(self, generations_per_second: float) -> None:
        self.gps = max(0.1, min(240.0, float(generations_per_second)))
        self.budget = min(self.budget, 1.0)

    def skip(self, generations: int) -> None:
        self.skip_remaining = max(0, min(50_000_000, int(generations)))
        self.skip_rate = 0.0

    def clear(self) -> None:
        self.editor.checkpoint(self._world_state())
        self.world.clear()
        self.world_name = ""
        self.skip_remaining = 0
        self.paused = True

    def reseed(self, density: float | None = None) -> None:
        self.editor.checkpoint(self._world_state())
        if (self.world.w, self.world.h) != self._config_size:
            # A soup belongs to the everyday world, not to the digital clock's
            # 60 million cells.
            self._resize_world(*self._config_size)
            self.camera.fit()
            self.camera.center()
            self.gps = float(self.cfg.generations_per_second)
            self.say(t("panel.soup.default", w=self.world.w, h=self.world.h))
        self.world_name = ""
        self.world.generation = 0
        self.world.seed_soup(self.cfg.density if density is None else max(0.01, min(0.95, float(density))))
        self.skip_remaining = 0
        if density is not None:
            self.cfg.density = float(density)

    # -- world size and saved worlds ------------------------------------------------
    def _world_state(self) -> dict:
        """What a world is besides its cells -- so undoing a load or a soup puts it all back."""
        return {"world_name": self.world_name, "gps": self.gps, "camera": self.camera.state(),
                "palette": self.look.palette.name}

    def restore_world_state(self, state: dict) -> None:
        self.world_name = str(state.get("world_name") or "")
        gps = _number(state.get("gps"), float, self.gps)
        self.set_speed(gps)
        if isinstance(state.get("camera"), dict):
            self.camera.restore(state["camera"])
        palette = state.get("palette")
        if isinstance(palette, str) and palette in palettes.names() and palette != self.look.palette.name:
            self.set_palette(palette)
        self.panel.post("worlds")

    def _resize_world(self, width: int, height: int) -> None:
        """Give the world a new size (it comes back empty) and tell everything that cares."""
        width = max(64, min(MAX_WORLD, int(width)))
        height = max(16, min(MAX_WORLD, int(height)))
        if (World.fit_width(width), height) != (self.world.w, self.world.h):
            self.world.resize(width, height)
            self.log.info("world resized to %dx%d", self.world.w, self.world.h)
        self.world_resized()

    def world_resized(self) -> None:
        """The world changed size (a resize, or an undo across one)."""
        world = self.world
        self.editor.world_resized()
        if (self.camera.world_w, self.camera.world_h) != (world.w, world.h):
            self.camera.set_world(world.w, world.h)
        self.renderer_reset(mature=True)
        self._dirty = True

    def _fitting_size(self, width: int, height: int) -> tuple[int, int]:
        """A world that holds a width x height pattern with room around it."""
        margin_w = max(64, width // 8)
        margin_h = max(64, height // 8)
        return (min(MAX_WORLD, max(self._config_size[0], width + 2 * margin_w)),
                min(MAX_WORLD, max(self._config_size[1], height + 2 * margin_h)))

    def _showable(self, width: int, height: int) -> tuple[int, int] | None:
        """The smallest world in which some zoom shows a width x height rectangle whole.

        The camera never shows more than the world -- the torus would be seen
        repeating -- so a world only just big enough for a pattern may not be
        allowed to zoom out far enough to show it.  Empty space is cheap.
        None when no world up to the largest size would do.
        """
        cam = self.camera
        for zoom in reversed(Camera.LEVELS):
            if zoom >= 1:
                span_w, span_h = cam.screen_w / zoom, cam.screen_h / zoom
                need, room = (math.ceil(span_w), math.ceil(span_h)), 0
            else:
                k = round(1 / zoom)
                span_w, span_h = cam.screen_w * k, cam.screen_h * k
                need, room = (span_w, span_h), k            # the view starts on a block boundary
            if span_w >= width + room and span_h >= height + room:
                if need[0] > MAX_WORLD or need[1] > MAX_WORLD:
                    return None
                return need
        return None

    def apply_world(self, cells, rule: str, meta: dict, name: str) -> None:
        """Replace the world with ``cells``, sized and framed as ``meta`` asks.

        Everything in ``meta`` is checked before the world is touched: world
        files are text anyone can edit, and a bad value must never leave a
        half-loaded world behind.
        """
        meta = meta if isinstance(meta, dict) else {}
        full_h, full_w = cells.shape
        rows, cols = np.flatnonzero(cells.any(axis=1)), np.flatnonzero(cells.any(axis=0))
        if rows.size:
            top, left = int(rows[0]), int(cols[0])
            live = cells[top:int(rows[-1]) + 1, left:int(cols[-1]) + 1]
        else:
            top = left = 0
            live = cells[:0, :0]
        live_h, live_w = live.shape
        if live_w > MAX_WORLD or live_h > MAX_WORLD:
            self.say(t("worlds.too_big", w=num(live_w), h=num(live_h), max=num(MAX_WORLD)))
            return
        rule_note = ""
        if rule:
            try:
                rule = normalise_rule(rule)
            except ValueError as exc:
                rule_note = t("worlds.bad_rule", err=exc, rule=self.world.rule)
                self.log.warning("the world %r asks for an unsupported rule: %s", name, exc)
                rule = ""
        width = _number(meta.get("width"), int, 0, 0, MAX_WORLD)
        height = _number(meta.get("height"), int, 0, 0, MAX_WORLD)
        placed_x = _number(meta.get("x"), int, None, -MAX_WORLD, MAX_WORLD)
        placed_y = _number(meta.get("y"), int, None, -MAX_WORLD, MAX_WORLD)
        generation = _number(meta.get("generation"), int, 0, 0, 2 ** 62)
        gps = _number(meta.get("gps"), float, 0.0, 0.0, 240.0)
        view = _rect(meta.get("view"))
        focus = _rect(meta.get("focus"))
        camera = meta.get("camera") if isinstance(meta.get("camera"), dict) else None
        if width < live_w or height < live_h:
            width, height = self._fitting_size(live_w, live_h)
        # What has to fit on the screen: the file's view, else everything.
        target = view or (0, 0, live_w, live_h)
        need = None if camera else self._showable(target[2], target[3])
        if need is None and not camera and focus is not None:
            target, need = focus, self._showable(focus[2], focus[3])
        if need is not None:
            width, height = max(width, need[0]), max(height, need[1])

        self.editor.checkpoint(self._world_state())
        self._resize_world(width, height)
        world = self.world
        if placed_x is not None and placed_y is not None:
            x, y = placed_x + left, placed_y + top
        elif full_w <= world.w and full_h <= world.h:
            x, y = (world.w - full_w) // 2 + left, (world.h - full_h) // 2 + top
        else:
            x, y = (world.w - live_w) // 2, (world.h - live_h) // 2
        world.clear()
        if live.size:
            world.put_region(live, x, y, "replace")
        world.generation = generation
        world.epoch += 1
        if rule:
            self.set_rule(rule)
        if camera is not None:
            self.camera.restore(camera)
        elif view is not None or focus is not None:
            self.camera.frame(target)
        else:
            self._look_at((x, y, live_w, live_h))
        if gps:
            self.set_speed(gps)
        palette = meta.get("palette")
        if isinstance(palette, str) and palette in palettes.names() and palette != self.look.palette.name:
            self.set_palette(palette)
        self.skip_remaining = 0
        self.world_name = name
        self.renderer_reset(mature=True)
        self._save_world()
        self.say(t("worlds.loaded", name=name, w=num(world.w), h=num(world.h)) + rule_note)
        self.log.info("loaded the world %r (%dx%d, %s cells)", name, world.w, world.h, f"{world.population:,}")
        self.panel.post("worlds")

    def _look_at(self, rect: tuple[int, int, int, int]) -> None:
        """Show a rectangle of cells: centred if it fits at this zoom, framed if not."""
        cam = self.camera
        x, y, w_, h = rect
        if w_ * cam.zoom <= cam.screen_w and h * cam.zoom <= cam.screen_h:
            cam.pan_cells(x + w_ / 2 - (cam.x + cam.screen_w / cam.zoom / 2),
                          y + h / 2 - (cam.y + cam.screen_h / cam.zoom / 2))
        else:
            cam.frame(rect)

    def load_world_file(self, path: str, name: str = "") -> None:
        try:
            pattern, meta = worlds.read(path)
        except (OSError, rle.PatternError) as exc:
            self.say(t("worlds.fail", err=exc))
            return
        title = name or worlds.title_of(meta, os.path.splitext(os.path.basename(path))[0], text_language())
        self.apply_world(pattern.cells, pattern.rule, meta, title)

    def save_world_named(self, name: str) -> None:
        name = " ".join(name.split())
        if not name:
            return
        path = worlds.path_for(name)
        meta = {"width": self.world.w, "height": self.world.h, "generation": self.world.generation,
                "gps": round(self.gps, 2), "camera": self.camera.state()}
        try:
            worlds.write(path, self.world.to_array(), self.world.rule, name, meta)
        except OSError as exc:
            self.say(str(exc))
            return
        self.world_name = path.stem
        self.say(t("worlds.saved", name=path.stem))
        self.log.info("saved the world as %s", path)
        self.panel.post("worlds", path.stem)        # only now is the file there to list

    def delete_world(self, path: str) -> None:
        if worlds.delete(path):
            self.log.info("deleted the world %s", path)
            if self.world_name == os.path.splitext(os.path.basename(path))[0]:
                self.world_name = ""
            self.panel.post("worlds", "")
        else:
            self.say(t("worlds.cannot_delete"))

    def open_worlds_folder(self) -> None:
        worlds.open_folder()

    def open_patterns_folder(self) -> None:
        folder = library.user_folder()
        if folder is not None:
            library.open_folder(folder)

    def load_pattern_as_world(self, key: str) -> None:
        """Open a library pattern as the whole world, sized to fit it."""
        try:
            cells = library.cells(key)
        except (KeyError, rle.PatternError, OSError) as exc:
            self.say(t("worlds.fail", err=exc))
            return
        entry = library.get().get(key)
        width, height = self._fitting_size(cells.shape[1], cells.shape[0])
        self.apply_world(cells, "", {"width": width, "height": height},
                         entry.name if entry is not None else key)

    def keep_selection(self, name: str) -> None:
        """Save the selected cells into the user's pattern folder."""
        cells = self.editor.selected_cells()
        folder = library.user_folder()
        if cells is None or folder is None or not cells.any():
            self.say(t("panel.sel.empty"))
            return
        name = " ".join(name.split())
        stem = library.safe_filename(name or "pattern")
        path = folder / f"{stem}.rle"
        n = 2
        while path.exists():
            path = folder / f"{stem} ({n}).rle"
            n += 1
        try:
            persistence.atomic_write(path, rle.to_rle(rle.crop(cells), self.world.rule, name=name or stem)
                                     .encode("utf-8"))
        except OSError as exc:
            self.say(str(exc))
            return
        self.say(t("lib.kept", name=path.stem))
        self.panel.post("library")

    def renderer_reset(self, mature: bool = False) -> None:
        if self.renderer is not None:
            self.renderer.request_reset(min(255.0, self.cfg.age_span * 2.0) if mature else 1.0)

    def view_changed(self) -> None:
        self._dirty = True

    def zoom_by(self, steps: int) -> None:
        self.camera.zoom_by(int(steps))

    def fit_view(self) -> None:
        self.camera.fit()

    def accent_rgb(self) -> tuple[int, int, int]:
        return tuple(self.look.accent())

    def set_palette(self, name: str, fade: float = 2.0) -> None:
        names = palettes.names()
        if name in names:
            self._palette_index = names.index(name)
            self.look.set_palette(palettes.get(name), fade)
            self.cfg.palette = name
            self._palette_due = time.monotonic() + max(0.5, self.cfg.cycle_minutes) * 60
            self.panel.post("accent", self.accent_rgb())

    def next_palette(self) -> str:
        names = palettes.names()
        self.set_palette(names[(self._palette_index + 1) % len(names)])
        return names[self._palette_index]

    def set_cycle(self, on: bool) -> None:
        self.cfg.cycle_palettes = bool(on)
        self._palette_due = time.monotonic() + max(0.5, self.cfg.cycle_minutes) * 60

    def set_grid(self, on: bool) -> None:
        self.cfg.grid = self.grid.enabled = bool(on)

    def set_smooth(self, on: bool) -> None:
        self.cfg.smooth = bool(on)

    def set_trails(self, seconds: float) -> None:
        self.cfg.trail_seconds = self.look.trail_seconds = max(0.0, min(30.0, float(seconds)))

    def set_editor_frame(self, on: bool) -> None:
        self.cfg.editor_frame = bool(on)

    def set_rule(self, rule: str) -> None:
        try:
            rule = normalise_rule(rule)
        except ValueError as exc:
            self.say(str(exc))
            return
        if rule != self.world.rule:
            self.world.set_rule(rule)
            self.cfg.rule = rule
            self.log.info("rule changed to %s", rule)
            self.say(f"{t('panel.rule')}: {rule}")

    def set_wallpaper_visible(self, visible: bool) -> None:
        self.wallpaper_visible = bool(visible)
        for win, surface, monitor in self.surfaces:
            surface.shown = self.wallpaper_visible
            if self.layer is not None and self.layer.kind != "window":
                if visible:
                    host.place(self.layer, win.hwnd, monitor, show=True)
                else:
                    w.ShowWindow(win.hwnd, w.SW_HIDE)
        if not visible:
            host.reveal_wallpaper(self.layer)     # the Windows wallpaper, not black
        self._dirty = True
        if self.tray is not None:
            self.tray.update()
        self.log.info("wallpaper %s", "shown" if visible else "hidden")

    def toggle_wallpaper_visible(self) -> bool:
        self.set_wallpaper_visible(not self.wallpaper_visible)
        return self.wallpaper_visible

    def startup_enabled(self) -> bool:
        return self._startup

    def set_startup(self, enabled: bool) -> None:
        if persistence.set_startup(bool(enabled)):
            self._startup = persistence.startup_enabled()
            self.log.info("start with Windows: %s", "on" if self._startup else "off")

    def save_settings(self) -> None:
        cfg = self.cfg
        cfg.generations_per_second = round(self.gps, 2)
        zoom = self.camera.zoom
        cfg.zoom = int(zoom) if zoom >= 1 else round(zoom, 4)    # 0.3333 snaps back to 1/3
        cfg.brush_size = self.editor.brush_size
        try:
            path = cfg.save()
            self._config_rule = cfg.rule
            self.say(t("panel.saved", path=path))
            self.log.info("settings saved to %s", path)
        except OSError as exc:
            self.say(str(exc))

    def open_settings(self) -> None:
        path = self.cfg.path or str(Config.default_path())
        if not os.path.exists(path):
            self.cfg.save(path)
        os.startfile(path)                                  # noqa: S606 - the user's own file

    def open_log(self) -> None:
        path = persistence.data_dir() / "golwall.log"
        if path.exists():
            os.startfile(str(path))                         # noqa: S606

    def remember_panel(self, x: int, y: int) -> None:
        persistence.save_state(panel=[int(x), int(y)])

    def panel_position(self) -> tuple[int, int] | None:
        pos = persistence.load_state().get("panel")
        if isinstance(pos, list) and len(pos) == 2:
            return int(pos[0]), int(pos[1])
        return None

    def save_world_as(self, path: str) -> None:
        """Export the whole world as RLE (with golwall's notes, so it opens back the same)."""
        meta = {"width": self.world.w, "height": self.world.h, "generation": self.world.generation,
                "gps": round(self.gps, 2), "camera": self.camera.state()}
        try:
            worlds.write(path, self.world.to_array(), self.world.rule,
                         self.world_name or os.path.splitext(os.path.basename(path))[0], meta)
            self.say(t("panel.saved", path=path))
        except OSError as exc:
            self.say(str(exc))

    def load_world_text(self, text: str, name: str = "") -> None:
        """Open any pattern file as the world; one bigger than the world makes it grow."""
        try:
            pattern = rle.parse(text)
        except rle.PatternError as exc:
            self.say(t("panel.import.fail", err=exc))
            return
        meta = {}
        for line in text.splitlines()[:64]:
            found = worlds.parse_meta_line(line)
            if found is not None:
                meta = found
                break
        cells = pattern.cells
        if not meta:
            if cells.shape[0] <= self.world.h and cells.shape[1] <= self.world.w:
                meta = {"width": self.world.w, "height": self.world.h}
            else:
                cells = rle.crop(cells)
        self.apply_world(cells, pattern.rule, meta, name or pattern.name or t("worlds"))

    def _save_world(self) -> None:
        if not self.cfg.restore_world:
            return
        state = {**self.camera.state(), "config_rule": self._config_rule,
                 "config_size": list(self._config_size), "world_name": self.world_name,
                 "gps": round(self.gps, 2)}
        try:
            persistence.save_world(self.world, state)
        except Exception:
            self.log_exception("saving the world")

    def snapshot(self) -> dict:
        """Plain values for the control panel, which reads them from its own thread."""
        editor = self.editor
        clip = editor.clipboard
        layer = self.layer
        return {
            "generation": self.world.generation, "population": self.world.population,
            "gps": self.gps, "paused": self.paused, "fps": self.fps, "zoom": self.camera.zoom,
            "palette": self.look.palette.name, "cycle": self.cfg.cycle_palettes,
            "cycle_minutes": self.cfg.cycle_minutes, "grid": self.grid.enabled, "smooth": self.cfg.smooth,
            "trails": self.look.trail_seconds, "frame": self.cfg.editor_frame,
            "rule": self.world.rule, "density": self.cfg.density,
            "tool": editor.tool, "brush": editor.brush_size, "selection": editor.selection,
            "clipboard": None if clip is None else clip.shape, "can_undo": editor.can_undo,
            "message": self.message, "message_serial": self.message_serial,
            "skip_remaining": self.skip_remaining, "skip_rate": self.skip_rate,
            "visible": self.wallpaper_visible, "startup": self._startup,
            "monitors": len(self.surfaces), "interactive": self.interactive,
            "layer": layer.kind if layer is not None else "none",
            "world_size": (self.world.w, self.world.h), "world_name": self.world_name,
            "default_size": self._config_size,
        }

    # ================================================================ shutdown ==
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.running = False
        def close_panel():
            self.panel_closed = self.panel.shutdown()

        steps = (("mouse hook", self.mouse.stop), ("control panel", close_panel),
                 ("saving the world", self._save_world),
                 ("tray icon", lambda: self.tray.remove() if self.tray is not None else None),
                 ("hotkey", lambda: w.UnregisterHotKey(self.control.hwnd, HOTKEY_ID)),
                 ("notifications", self._unregister_notifications),
                 ("surfaces", self._detach),
                 ("renderer", lambda: self.renderer.release() if self.renderer is not None else None),
                 ("window", lambda: self.window.destroy() if self.window is not None else None),
                 ("control window", self.control.destroy),
                 ("timer", self.waiter.close))
        for what, step in steps:
            try:
                step()
            except Exception:
                self.log_exception(f"closing: {what}")
        self.tray = None
        self.renderer = None
        self.log.info("stopped")
