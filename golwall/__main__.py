"""Command line entry point: ``py -3 -m golwall`` (or ``main.py``, or the .exe)."""

from __future__ import annotations

import argparse
import ctypes
import sys
import threading
import time
from pathlib import Path

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="golwall",
        description="Conway's Game of Life as a live, drawable Windows wallpaper.")
    p.add_argument("-w", "--windowed", action="store_true",
                   help="run in an ordinary window instead of on the desktop")
    p.add_argument("--editor", action="store_true", help="open the editor right away")
    p.add_argument("--autostart", action="store_true",
                   help="launched at sign-in: if a copy is already running, just exit")
    p.add_argument("--config", metavar="PATH", help="settings file to use")
    p.add_argument("--seconds", type=float, metavar="N",
                   help="quit after N seconds (useful for a quick smoke test)")
    p.add_argument("--zoom", type=float,
                   help="override the starting zoom, in pixels per cell (0.5 = two cells per pixel)")
    p.add_argument("--palette", help="override the starting palette")
    p.add_argument("--world", metavar="WxH", help="override the world size, e.g. 1024x576")
    p.add_argument("--attach", choices=("auto", "progman", "workerw", "bottom"),
                   help="force a desktop layer instead of detecting it")
    p.add_argument("--no-tray", action="store_true", help="do not show a tray icon")
    p.add_argument("--quiet", action="store_true", help="only print warnings")
    p.add_argument("--allow-multiple", action="store_true",
                   help="start even if another copy is already running")
    p.add_argument("--log-file", metavar="PATH", help="write the log here instead")
    p.add_argument("--list-palettes", action="store_true")
    p.add_argument("--list-patterns", action="store_true",
                   help="list the pattern library by category (with a word: search it)")
    p.add_argument("--search", metavar="WORDS", help="with --list-patterns: only names with these words")
    p.add_argument("--list-worlds", action="store_true", help="list the saved and example worlds")
    p.add_argument("--open-world", metavar="NAME",
                   help="start with a saved world (its name, e.g. \"Relógio digital\", or a file)")
    p.add_argument("--write-config", action="store_true",
                   help="write the current settings to the config file and exit")
    p.add_argument("--install-startup", action="store_true", help="start with Windows, then exit")
    p.add_argument("--uninstall-startup", action="store_true", help="stop starting with Windows, then exit")
    p.add_argument("--diagnose", action="store_true",
                   help="report what the wallpaper would do on this desktop, then exit")
    p.add_argument("--version", action="version", version=f"golwall {__version__}")
    return p


def apply_overrides(cfg, args) -> list[str]:
    problems = []
    if args.zoom:
        zoom = max(0.0, min(64.0, args.zoom))
        cfg.zoom = zoom if zoom < 1 else int(round(zoom))
    if args.palette:
        cfg.palette = args.palette
    if args.no_tray:
        cfg.tray_icon = False
    if args.attach:
        cfg.attach_mode = args.attach
    if args.world:
        try:
            width, height = (int(v) for v in args.world.lower().split("x"))
            problems += cfg.apply({"world_width": width, "world_height": height})
        except ValueError:
            raise SystemExit(f"--world expects WxH, got {args.world!r}")
    return problems


def _hand_over_to_running_copy() -> bool:
    """Ask the copy that is already running to open its editor."""
    from .app import CONTROL_CLASS, WM_OPEN_EDITOR
    from .native import win32 as w
    hwnd = w.FindWindowW(CONTROL_CLASS, None)
    if not hwnd:
        return False
    try:
        ctypes.windll.user32.AllowSetForegroundWindow(w.process_of(hwnd))
    except (AttributeError, OSError):
        pass
    return bool(w.PostMessageW(hwnd, WM_OPEN_EDITOR, 0, 0))


def diagnose(cfg) -> int:
    from .capabilities import host, persistence
    from .capabilities.power import Occlusion, PowerPolicy, SystemState
    from .native import win32 as w
    print(f"golwall {__version__} on Windows build {persistence.windows_build()}")
    print(f"python {sys.version.split()[0]}, data folder {persistence.data_dir()}")
    print(f"monitors: {w.describe_monitors()}; virtual screen {w.virtual_screen()}")
    for mode in ("auto", "progman", "workerw"):
        layer = host.find_layer(mode)
        print(f"layer {mode:8s}: {layer.describe() if layer else 'not available'}"
              + (f"  {layer}" if layer else ""))
    print(f"learned layer: {persistence.learned_layer()}")
    try:
        from .render.renderer import Renderer
        renderer = Renderer(cfg.world_width if cfg.world_width % 64 == 0 else 1280, cfg.world_height)
        print(f"renderer: Direct3D 11 on {renderer.driver}, feature level {renderer.device.feature_level:X}")
        renderer.release()
    except Exception as exc:
        print(f"renderer: FAILED - {exc}")
    policy = PowerPolicy(cfg.pause_when_busy)
    print(f"full-screen check: {policy.check().reason}")
    rects = [(m[0], m[1], m[0] + m[2], m[1] + m[3]) for m in w.monitors()]
    print(f"desktop visible per monitor: {Occlusion().visible(rects)}")
    state = SystemState()
    state.refresh_static()
    print(f"system: {state}")
    print(f"start with Windows: {persistence.startup_enabled()}")
    return 0


QUERY_FLAGS = frozenset({"--diagnose", "--list-palettes", "--list-patterns", "--list-worlds", "--version",
                         "--help", "-h", "--install-startup", "--uninstall-startup", "--write-config"})


def find_world(name: str):
    """A world by its title, file name or path; None when there is no such world."""
    from .capabilities import worlds
    path = Path(name)
    if path.suffix and path.is_file():
        return path, ""
    wanted = name.strip().lower()
    for language in ("pt", "en"):
        for item in worlds.list_worlds(language):
            if wanted in (item.name.lower(), item.path.stem.lower(), item.path.name.lower()):
                return item.path, item.name
    return None


def _attach_parent_console() -> None:
    """Let the windowed .exe print answers when it was started from a terminal."""
    try:
        if ctypes.windll.kernel32.AttachConsole(-1):          # ATTACH_PARENT_PROCESS
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
            sys.stderr = sys.stdout
    except (AttributeError, OSError):
        pass


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    if sys.stdout is None and QUERY_FLAGS.intersection(raw):
        _attach_parent_console()
    args = build_parser().parse_args(argv)

    if args.list_palettes:
        from .core import palettes
        for name in palettes.names():
            print(name)
        return 0
    if args.list_patterns:
        from .core import library
        from .ui.text import category
        lib = library.get()
        if lib.error:
            print(f"the pattern library could not be read ({lib.error}); only the classics are available")
        groups = [("search", lib.search(args.search))] if args.search else lib.categories()
        for cat, items in groups:
            print(f"{category(cat) if cat != 'search' else args.search!r}: {len(items)}")
            for e in items if (args.search or cat == "classic") else items[:8]:
                period = f"p{e.period}" if e.period else ""
                print(f"  {e.key:28s} {e.name[:36]:36s} {e.width}x{e.height} {period}")
            if not args.search and cat != "classic" and len(items) > 8:
                print(f"  ... and {len(items) - 8} more")
        return 0
    if args.list_worlds:
        from .capabilities import worlds
        from .ui import text
        text.set_language("auto")
        for item in worlds.list_worlds(text.language()):
            size = f"{item.meta.get('width', '?')}x{item.meta.get('height', '?')}"
            print(f"{'example' if item.starter else 'saved  '}  {item.name:32s} {size:12s} {item.path}")
        return 0

    from .native import win32 as w
    dpi = w.enable_dpi_awareness()                   # before any window exists

    from . import log as log_module
    from .capabilities import persistence
    from .capabilities.persistence import Config
    from .ui import text

    cfg, warnings = Config.load(args.config)
    warnings += apply_overrides(cfg, args)
    text.set_language(cfg.language)

    if args.write_config:
        path = cfg.save(args.config)
        print(f"wrote {path}")
        return 0
    if args.install_startup or args.uninstall_startup:
        ok = persistence.set_startup(bool(args.install_startup))
        print(("will start with Windows" if args.install_startup else "will not start with Windows")
              if ok else "could not change the startup setting")
        return 0 if ok else 1
    if args.diagnose:
        return diagnose(cfg)

    log_path = Path(args.log_file) if args.log_file else persistence.data_dir() / "golwall.log"
    logger = log_module.setup(log_path, args.quiet)
    for warning in warnings:
        logger.warning("config: %s", warning)

    from .core import palettes
    if cfg.palette not in palettes.names():
        logger.warning("unknown palette %r; using %r", cfg.palette, palettes.names()[0])
        cfg.palette = palettes.names()[0]

    if not args.allow_multiple and not w.claim_single_instance():
        if args.autostart:
            logger.info("another copy is already running")
        elif _hand_over_to_running_copy():
            logger.info("another copy is already running; asked it to open the editor")
        else:
            logger.info("another copy is already running")
        log_module.shutdown()
        return 0

    from .app import Wallpaper
    logger.info("golwall %s starting (Windows build %s, DPI awareness %s): world %dx%d, rule %s, "
                "%g gen/s, %d fps, palette %s", __version__, persistence.windows_build(), dpi,
                cfg.world_width, cfg.world_height, cfg.rule, cfg.generations_per_second, cfg.fps,
                cfg.palette)
    app = None
    try:
        app = Wallpaper(cfg, windowed=args.windowed, log=logger)
        if args.seconds:
            def stopper():
                time.sleep(args.seconds)
                app.stop()
            threading.Thread(target=stopper, name="golwall-stopper", daemon=True).start()
        if args.open_world:
            found = find_world(args.open_world)
            if found is None:
                logger.warning("there is no world called %r (see --list-worlds)", args.open_world)
            else:
                app.call(app.load_world_file, str(found[0]), found[1])
        if args.editor:
            app.call(app.set_interactive, True)
        app.run()
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.exception("the wallpaper stopped because of an error")
        return 1
    finally:
        if app is not None:
            app.close()
        stuck = app is not None and not app.panel_closed
        if stuck:
            logger.warning("the control panel did not close in time (a dialog was open?)")
        log_module.shutdown()
        if stuck:
            # Tk objects must die on their own thread; letting the interpreter
            # tear them down from this one would abort instead of exiting.
            import os
            os._exit(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
