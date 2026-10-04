"""Self-checks for the simulation, the formats, the renderer and the plumbing.

Run with ``venv\\Scripts\\python tests\\test_wallpaper.py``.  Nothing here needs a visible
desktop: the renderer is checked offscreen against a CPU reference, and the
window test creates and destroys hidden windows to pin down a lifetime bug
that used to kill the process.
"""

from __future__ import annotations

import sys
from pathlib import Path
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))      # runnable as a script from any folder

from golwall.core import palettes, patterns, rle
from golwall.core.camera import Camera, Viewport
from golwall.core.life import World, blit_wrapped, normalise_rule, parse_rule

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "ok  " if condition else "FAIL"
    print(f"{status} {name}" + (f"   {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def crop(grid: np.ndarray) -> tuple[np.ndarray | None, tuple[int, int]]:
    ys, xs = np.nonzero(grid)
    if ys.size == 0:
        return None, (0, 0)
    return (grid[ys.min():ys.max() + 1, xs.min():xs.max() + 1].copy(),
            (int(xs.min()), int(ys.min())))


def reference_step(cells: np.ndarray, birth, survive) -> np.ndarray:
    """The textbook rule, one byte per cell -- slow, obviously right."""
    n = sum(np.roll(np.roll(cells, dy, 0), dx, 1)
            for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dy, dx) != (0, 0))
    born = np.isin(n, list(birth)) & (cells == 0)
    keep = np.isin(n, list(survive)) & (cells == 1)
    return (born | keep).astype(np.uint8)


# ================================================================ simulation ===
def test_rules() -> None:
    check("rule B3/S23 parses", parse_rule("B3/S23") == (frozenset({3}), frozenset({2, 3})))
    check("rule B36/S23 parses", parse_rule("B36/S23") == (frozenset({3, 6}), frozenset({2, 3})))
    check("rule without a slash parses", parse_rule("b3s23") == parse_rule("B3/S23"))
    check("Seeds (no survival) parses", parse_rule("B2/S") == (frozenset({2}), frozenset()))
    check("rules are normalised", normalise_rule("S32/B3") == "B3/S23")
    for bad in ("", "X3", "B3/Snope", "B9/S23", "B3"):
        try:
            parse_rule(bad)
            check(f"rule {bad!r} rejected", False)
        except ValueError:
            check(f"rule {bad!r} rejected", True)


def test_engine_matches_reference() -> None:
    rng = np.random.default_rng(3)
    bad = []
    rules = ("B3/S23", "B36/S23", "B2/S", "B3678/S34678", "B1357/S1357", "B0123478/S01234678",
             "B368/S245", "B8/S8")
    for rule in rules:
        birth, survive = parse_rule(rule)
        for width, height in ((64, 3), (128, 77), (192, 64)):
            world = World(width, height, rule)
            cells = (rng.random((world.h, world.w)) < 0.35).astype(np.uint8)
            world.load_array(cells)
            for gen in range(8):
                before = cells
                stats = world.step()
                cells = reference_step(cells, birth, survive)
                born = int(((cells == 1) & (before == 0)).sum())
                if (not np.array_equal(world.to_array(), cells) or stats.population != int(cells.sum())
                        or stats.born != born or stats.died != int(((cells == 0) & (before == 1)).sum())):
                    bad.append((rule, width, height, gen))
                    break
    check("bit-packed engine matches the reference for every rule", not bad, str(bad[:3]))


def test_patterns() -> None:
    for info in patterns.LIBRARY:
        art = patterns.get(info.key)
        world = World(art.shape[1] + 80, art.shape[0] + 80)
        world.stamp(art, 40, 40)
        start, origin = crop(world.to_array())
        if info.period:
            for _ in range(info.period):
                world.step()
            now, moved_to = crop(world.to_array())
            same = now is not None and np.array_equal(start, now)
            check(f"{info.key} has period {info.period}", same and (moved_to != origin) == info.moves)
        else:
            pops = {world.step().population for _ in range(60)}
            check(f"{info.key} stays chaotic", len(pops) > 3, f"{len(pops)} distinct populations")


def test_torus() -> None:
    src = np.arange(16, dtype=np.uint8).reshape(4, 4)
    dst = np.zeros((3, 3), np.uint8)
    blit_wrapped(src, dst, 3, 3)
    expected = np.array([[15, 12, 13], [3, 0, 1], [7, 4, 5]], np.uint8)
    check("toroidal window wraps on both axes", np.array_equal(dst, expected))

    world = World(64, 30)
    world.stamp(patterns.get("glider"), 62, 28)          # straddles both seams
    before = world.population
    for _ in range(40):
        world.step()
    check("a glider crosses the seams intact", world.population == before,
          f"{before} cells before, {world.population} after 40 generations")
    check("widths round up to whole words", World(100, 10).w == 128)


def test_regions() -> None:
    world = World(128, 50)
    art = np.array([[1, 0, 1], [1, 1, 0]], np.uint8)
    world.put_region(art, 127, 49)
    cells = world.to_array()
    check("a region written across both seams lands wrapped",
          cells[49, 127] == 1 and cells[49, 1] == 1 and cells[0, 127] == 1 and cells[0, 0] == 1
          and cells[49, 0] == 0 and cells[0, 1] == 0)
    check("reading it back wraps the same way", np.array_equal(world.get_region(127, 49, 3, 2), art))
    world.put_region(np.ones((2, 3), np.uint8), 127, 49, "erase")
    check("erasing clears exactly those cells", world.population == 0)
    world.seed_soup(1.0, (10, 10, 20, 15))
    check("a soup region fills only its rectangle", world.population == 50, f"{world.population}")
    world.clear_region(10, 10, 5, 5)
    check("clearing part of it leaves the rest", world.population == 25, f"{world.population}")
    version = world.version
    world.stamp(patterns.get("block"), 0, 0)
    check("every edit bumps the version", world.version > version)


def test_snapshot_restore() -> None:
    world = World(64, 32, seed=1)
    world.seed_soup(0.3)
    snap = world.snapshot()
    epoch = world.epoch
    for _ in range(10):
        world.step()
    world.restore(snap)
    check("restore brings back the exact cells", np.array_equal(world.bits, snap[0]))
    check("and the generation", world.generation == snap[1])
    check("and marks the history as broken", world.epoch > epoch)


def test_camera() -> None:
    bad = []
    for screen in [(4080, 2570), (1920, 1080), (3840, 2160), (1366, 768)]:
        for world in [(1024, 576), (768, 432), (2048, 1152), (1280, 720)]:
            cam = Camera(*screen, *world)
            for zoom in range(cam.fit_zoom(), 33):
                cam.set_zoom(zoom)
                for frac in (0.0, 0.5, 0.99):
                    cam.x, cam.y = 10 + frac, 20 + frac
                    vp = cam.viewport()
                    if screen[0] / zoom > world[0] or screen[1] / zoom > world[1]:
                        bad.append(("view wider than the world", screen, world, zoom))
                    if not (vp.cols <= world[0] and vp.rows <= world[1]):
                        bad.append(("more cells than the world", screen, world, zoom))
    check("every zoom from the fit in shows no more than the world", not bad, str(bad[:2]))
    check("4K fits a 1280x720 world exactly at 3 px per cell",
          Camera(3840, 2160, 1280, 720).fit_zoom() == 3)
    cam = Camera(3840, 2160, 1280, 720, zoom=3)
    check("the wheel can still zoom out past the fit, where the torus repeats",
          cam.zoom_by(-1) and cam.zoom == 2 and cam.min_zoom() == 1 / 2)

    cam = Camera(1920, 1080, 1024, 576, zoom=8)
    anchor = (700, 400)
    before = cam.screen_to_world(*anchor)
    cam.set_zoom(16, anchor)
    check("zooming keeps the cell under the pointer", before == cam.screen_to_world(*anchor))
    cam.pan_cells(-99999, 99999)
    check("panning wraps instead of hitting an edge", 0 <= cam.x < cam.world_w and 0 <= cam.y < cam.world_h)
    state = cam.state()
    other = Camera(1920, 1080, 1024, 576)
    other.restore(state)
    check("the camera round-trips through saved state", other.state() == state)


def test_palettes() -> None:
    from golwall.core.look import Look
    for name in palettes.names():
        palette = palettes.get(name)
        alive, trail = palettes.build_luts(palette)
        ok = alive.shape == (256, 4) and trail.shape == (256, 4)
        ok = ok and int(trail[0, :3].sum()) < int(alive[0, :3].sum())
        check(f"palette {name} builds a usable ramp", ok)
        check(f"palette {name} is black behind the cells",
              palette.bg == (0, 0, 0) and int(trail[0, :3].sum()) == 0)
    look = Look(palettes.get("matrix"))
    first = look.table.copy()
    look.set_palette(palettes.get("ember"), 1.0)
    look.advance(0.5)
    middle = look.table.copy()
    look.advance(0.6)
    check("a crossfade passes through a blend", not np.array_equal(first, middle)
          and not np.array_equal(middle, look.table))
    check("and ends on the new palette", not look.fading)
    check("trails decay to 2% after trail_seconds", abs(look.decay(look.trail_seconds) - 0.02) < 1e-9)


def test_glider_gun_runs_clean(generations: int = 6_000) -> None:
    """The user's case: a glider gun alone on a cleared world, left running.

    Nothing may add cells on its own -- the population has to grow by exactly
    one glider (5 cells) every 30 generations, forever, until the stream wraps
    round the torus and meets the gun some 46,000 generations later.
    """
    world = World(1280, 720, seed=11)
    world.seed_soup(0.16)
    for _ in range(50):
        world.step()
    world.clear()
    world.stamp(patterns.get("gosper_gun"), 525, 277)
    bad = []
    for t in range(1, generations + 1):
        stats = world.step()
        if t % 30 == 0 and stats.population != 36 + 5 * (t // 30):
            bad.append((t, stats.population))
    check(f"a lone glider gun emits one glider per 30 generations for {generations:,}", not bad,
          f"first mismatch {bad[:1]}" if bad else f"population {world.population}")
    empty = World(256, 128)
    grew = any(empty.step().population for _ in range(2_000))
    check("an empty world stays empty", not grew)


def test_speed() -> None:
    world = World(1280, 720, seed=1)
    world.seed_soup(0.2)
    for _ in range(10):
        world.step()
    started = time.perf_counter()
    for _ in range(200):
        world.step()
    per = (time.perf_counter() - started) / 200
    check("a 1280x720 generation takes under 2 ms", per < 0.002, f"{per * 1000:.2f} ms")


def test_grid_geometry() -> None:
    from golwall.core.grid import Grid

    grid = Grid(levels=(1, 8, 64))
    cam = Camera(1920, 1080, 1280, 720, zoom=20)
    lines = list(grid.lines(cam.viewport(), 1920, 1080))
    check("the grid is always white, whatever the palette",
          {line.colour for line in lines} == {(255, 255, 255)})
    check("it is drawn at the configured opacity",
          max(line.opacity for line in lines) == grid.opacity, f"{grid.opacity:.2f}")
    levels_at = lambda z: sum(1 for n in grid.levels if grid.opacity_for(n, z) > 0)   # noqa: E731
    check("the finest level fades out when zoomed out", levels_at(2) < levels_at(20),
          f"{levels_at(2)} levels at 2 px/cell, {levels_at(20)} at 20")
    grid.enabled = False
    check("the grid can be switched off", not list(grid.lines(cam.viewport(), 1920, 1080)))


# ================================================================== formats ===
GOSPER_RLE = """#N Gosper glider gun
#C This was the first gun discovered.
x = 36, y = 9, rule = B3/S23
24bo$22bobo$12b2o6b2o12b2o$11bo3bo4b2o12b2o$2o8bo5bo3b2o$2o8bo3bob2o4b
obo$10bo5bo7bo$11bo3bo$12b2o!
"""


def test_rle() -> None:
    glider = rle.parse("x = 3, y = 3\nbo$2bo$3o!")
    check("an RLE glider parses", np.array_equal(glider.cells, patterns.get("glider")))
    gun = rle.parse(GOSPER_RLE)
    check("a LifeWiki RLE parses with its name and rule",
          gun.name == "Gosper glider gun" and gun.rule == "B3/S23"
          and np.array_equal(gun.cells, patterns.get("gosper_gun")))
    plain = rle.parse("!Name: Glider\n.O.\n..O\nOOO\n")
    check("plaintext .cells parses", np.array_equal(plain.cells, patterns.get("glider")) and plain.name == "Glider")
    life106 = rle.parse("#Life 1.06\n0 -1\n1 0\n-1 1\n0 1\n1 1\n")
    check("Life 1.06 parses", np.array_equal(life106.cells, patterns.get("glider")))
    rng = np.random.default_rng(5)
    bad = 0
    for _ in range(40):
        cells = (rng.random((rng.integers(1, 40), rng.integers(1, 90))) < 0.3).astype(np.uint8)
        back = rle.parse(rle.to_rle(cells)).cells
        h, width = cells.shape
        padded = np.zeros((max(h, back.shape[0]), max(width, back.shape[1])), np.uint8)
        padded[:back.shape[0], :back.shape[1]] = back
        if not np.array_equal(padded[:h, :width], cells) or padded[h:].any() or padded[:, width:].any():
            bad += 1
    check("random patterns survive an RLE round trip", bad == 0, f"{bad} failed")
    check("lines stay under 71 characters", max(len(line) for line in rle.to_rle(np.ones((5, 300))).splitlines()) <= 70)
    for junk in ("hello world", "x = 99999, y = 99999\n!", ""):
        try:
            rle.parse(junk)
            check(f"junk {junk[:12]!r} rejected", False)
        except rle.PatternError:
            check(f"junk {junk[:12]!r} rejected", True)


# ============================================================== persistence ===
def test_config_validation() -> None:
    from golwall.capabilities.persistence import Config
    cfg = Config()
    warnings = cfg.apply({"fps": "60", "zoom": 2.0, "grid": "yes", "density": 7, "palette": 12,
                          "rule": "b36s23", "attach_mode": "sideways", "unknown": 1,
                          "grid_levels": [64, 1, 8, 8], "trail_seconds": "fast", "brush_size": 0})
    check("numbers given as text are accepted", cfg.fps == 60)
    check("whole floats become ints", cfg.zoom == 2 and isinstance(cfg.zoom, int))
    check("yes/no strings become booleans", cfg.grid is True)
    check("out-of-range values are clamped", cfg.density == 1.0 and cfg.brush_size == 1)
    check("the wrong type falls back to the default", cfg.palette == "matrix" and cfg.trail_seconds == 1.6)
    check("rules are normalised", cfg.rule == "B36/S23")
    check("unknown choices are rejected", cfg.attach_mode == "auto")
    check("grid levels are cleaned up", cfg.grid_levels == (1, 8, 64))
    check("every problem is reported", len(warnings) >= 6, f"{len(warnings)} warnings")


def test_world_persistence() -> None:
    import tempfile
    from pathlib import Path
    from golwall.capabilities import persistence
    world = World(128, 64, seed=3)
    world.seed_soup(0.3)
    for _ in range(7):
        world.step()
    original = persistence._DATA_DIR
    with tempfile.TemporaryDirectory() as folder:
        persistence._DATA_DIR = Path(folder)
        try:
            ok = persistence.save_world(world, {"x": 1.5, "y": 2.0, "zoom": 4})
            other = World(128, 64)
            camera = persistence.load_world(other)
            wrong = persistence.load_world(World(192, 64))
        finally:
            persistence._DATA_DIR = original
    check("a saved world loads back identically", ok and np.array_equal(other.bits, world.bits)
          and other.generation == world.generation and camera == {"x": 1.5, "y": 2.0, "zoom": 4})
    check("a world of another size is not forced in", wrong is None)


# ================================================================ decisions ===
def test_power_policy() -> None:
    from golwall.capabilities.power import NEVER_FULLSCREEN, PowerPolicy

    class Fake(PowerPolicy):
        def __init__(self, state, foreground, covers):
            super().__init__(True, frozenset({"GolWallpaperWindow"}))
            self._state, self._fg, self._covers = state, foreground, covers

        def shell_state(self):
            return self._state

        def foreground_class(self):
            return self._fg

        def foreground_covers_monitor(self):
            return self._covers

    check("a full-screen game pauses it", not Fake(3, "UnityWndClass", True).check())
    check("presentation mode pauses it", not Fake(4, "PowerPoint", False).check())
    check("an ordinary window does not", Fake(5, "Chrome_WidgetWin_1", False).check())
    check("a borderless full-screen app does", not Fake(2, "Chrome_WidgetWin_1", True).check())
    check("a maximised window with an auto-hidden taskbar does not",
          Fake(5, "Chrome_WidgetWin_1", True).check(),
          "it covers its monitor, but the shell does not call it full screen")
    check("clicking the desktop does not pause it", Fake(5, "Progman", True).check(),
          "Progman spans every monitor, so it always looks full screen")
    check("our own window does not pause it", Fake(2, "GolWallpaperWindow", True).check())
    check("pausing can be switched off", PowerPolicy(False).check())
    check("the desktop is never a full-screen app", "WorkerW" in NEVER_FULLSCREEN)


def test_occlusion_math() -> None:
    from golwall.capabilities.power import subtract, uncovered
    monitor = (0, 0, 1920, 1080)
    check("an uncovered monitor shows desktop", uncovered([monitor], []) == [True])
    check("a maximised window covers it", uncovered([monitor], [(-8, -8, 1928, 1088)]) == [False])
    check("a gap between two windows still counts",
          uncovered([monitor], [(0, 0, 900, 1080), (1020, 0, 1920, 1080)]) == [True])
    check("a sliver is not worth drawing for",
          uncovered([monitor], [(0, 0, 1915, 1080)]) == [False])
    check("each monitor is judged on its own",
          uncovered([monitor, (1920, 0, 3840, 1080)], [(0, 0, 1920, 1080)]) == [False, True])
    pieces = subtract([(0, 0, 10, 10)], (3, 3, 6, 6))
    area = sum((r - l) * (b - t) for l, t, r, b in pieces)
    check("subtracting a hole leaves the right area", area == 100 - 9, f"{area}")


def test_gesture_filter() -> None:
    from golwall.capabilities.input import DOWN, GestureFilter, MOVE, UP, WHEEL
    from golwall.native import win32 as w
    f = GestureFilter(lambda x, y: x < 100)          # x < 100 is "desktop"
    check("moves are never swallowed", not f.feed(w.WM_MOUSEMOVE, 10, 10))
    check("a press on the desktop is captured", f.feed(w.WM_LBUTTONDOWN, 10, 10))
    check("moves during it are recorded, not swallowed", not f.feed(w.WM_MOUSEMOVE, 150, 10)
          and f.events[-1][0] == MOVE)
    check("its release is captured even over a window", f.feed(w.WM_LBUTTONUP, 150, 10))
    check("a press on a window passes through", not f.feed(w.WM_RBUTTONDOWN, 150, 10))
    check("and so does its release", not f.feed(w.WM_RBUTTONUP, 150, 10))
    check("the wheel over the desktop zooms", f.feed(w.WM_MOUSEWHEEL, 10, 10, 120) and f.events[-1][0] == WHEEL)
    check("the wheel over a window scrolls it", not f.feed(w.WM_MOUSEWHEEL, 150, 10, 120))
    kinds = [e[0] for e in f.events]
    check("exactly the captured events were queued", kinds == [DOWN, MOVE, UP, WHEEL], str(kinds))

    # A release that never arrives (UAC, Ctrl+Alt+Del mid-press) must not leave
    # the hook swallowing clicks everywhere.
    f = GestureFilter(lambda x, y: x < 100)
    f.feed(w.WM_RBUTTONDOWN, 10, 10)                   # captured; its release is then lost
    check("after a lost release, a click on a window still goes through",
          not f.feed(w.WM_LBUTTONDOWN, 150, 10) and f.held == 0)
    check("and the editor is told the gesture ended", [e[0] for e in f.events][-1] == UP)
    f.feed(w.WM_LBUTTONDOWN, 10, 10)
    events_before = len(f.events)
    check("pressing a held button again restarts the gesture",
          f.feed(w.WM_LBUTTONDOWN, 20, 10) and f.held == 1
          and [e[0] for e in list(f.events)[events_before:]] == [UP, DOWN])


def test_uncover_wallpaper_guards() -> None:
    from golwall.capabilities import host
    check("the Windows wallpaper is only ever uncovered in the 24H2 layout",
          host.uncover_wallpaper(None) is False
          and host.uncover_wallpaper(host.DesktopLayer("window", 0)) is False
          and host.uncover_wallpaper(host.DesktopLayer("bottom", 0, 0)) is False)


def test_probe_judgement() -> None:
    from golwall.capabilities.host import PROBE_A, PROBE_B, VisibilityProbe
    probe = VisibilityProbe(minimum_points=4)
    probe.last_points = 10
    a = np.array([PROBE_A] * 10, np.uint8)
    b = np.array([PROBE_B] * 10, np.uint8)
    check("exact markers are recognised", probe.judge(a, b) and probe.last_hits == 10)
    hdr_a = np.clip(np.array(PROBE_A) * 1.8 + 6, 0, 255).astype(np.uint8)[None].repeat(10, 0)
    hdr_b = np.clip(np.array(PROBE_B) * 1.8 + 6, 0, 255).astype(np.uint8)[None].repeat(10, 0)
    check("brightened (HDR) markers are still recognised", probe.judge(hdr_a, hdr_b))
    still = np.array([[30, 30, 30]] * 10, np.uint8)
    check("a desktop that did not change is not ours", not probe.judge(still, still))


def test_hostile_patterns() -> None:
    started = time.perf_counter()
    rejected = 0
    for junk in ("2000000000$o!", "x = 3, y = 3\n" + "9" * 60 + "o!", "#Life 1.06\n1 two\n",
                 "#Life 1.06\n0 0\n99999999 0\n", "x = 99999, y = 99999\nbo!"):
        try:
            rle.parse(junk)
        except rle.PatternError:
            rejected += 1
    check("absurd patterns are refused before anything is allocated",
          rejected == 5 and time.perf_counter() - started < 0.5,
          f"{rejected}/5 in {time.perf_counter() - started:.3f}s")


def test_corrupt_world_file() -> None:
    import tempfile
    from pathlib import Path
    from golwall.capabilities import persistence
    original = persistence._DATA_DIR
    notes = []
    with tempfile.TemporaryDirectory() as folder:
        persistence._DATA_DIR = Path(folder)
        try:
            results = []
            for content in (b"", b"PK\x03\x04 truncated zip", b"garbage" * 100):
                persistence.world_path().write_bytes(content)
                results.append(persistence.load_world(World(128, 64), log=notes.append))
            quarantined = persistence.world_path().with_suffix(".bad.npz").exists()
        finally:
            persistence._DATA_DIR = original
    check("an unreadable world file means a fresh world, not a crash",
          results == [None, None, None] and len(notes) == 3 and quarantined, str(notes[:1]))


def test_text() -> None:
    from golwall.ui import text
    check("both languages have the same strings", set(text._EN) == set(text._PT),
          str(set(text._EN) ^ set(text._PT)))
    text.set_language("pt")
    pt = text.num(1234567)
    text.set_language("en")
    check("numbers follow the language", pt == "1.234.567" and text.num(1234567) == "1,234,567")


def test_icon() -> None:
    from golwall.ui import icon
    data = icon.ico_bytes()
    check("the icon file has every size", int.from_bytes(data[4:6], "little") == 9)
    check("its images are PNGs", data[6 + 16 * 9:6 + 16 * 9 + 8] == b"\x89PNG\r\n\x1a\n")


def test_editor() -> None:
    from golwall.capabilities.input import LEFT, MIDDLE, RIGHT
    from golwall.native import win32 as w
    from golwall.ui import editor as editor_module

    class FakeApp:
        def __init__(self):
            self.world = World(128, 64)
            self.camera = Camera(128 * 4, 64 * 4, 128, 64, zoom=4)
            self.camera.x = self.camera.y = 0.0
            self.cfg = type("Cfg", (), {"brush_size": 1})()
            self.control = type("Control", (), {"hwnd": 0})()
            self.messages = []

        def say(self, text):
            self.messages.append(text)

        def renderer_reset(self, mature=False):
            pass

        def view_changed(self):
            pass

    clipboard = {}
    real_set, real_get = w.set_clipboard_text, w.clipboard_text
    w.set_clipboard_text = lambda text, owner=0: clipboard.__setitem__("text", text) or True
    w.clipboard_text = lambda owner=0: clipboard.get("text")
    try:
        app = FakeApp()
        ed = editor_module.Editor(app)

        def px(cx, cy):
            sx, sy = app.camera.world_to_screen(cx, cy)
            return sx + 1, sy + 1

        ed.set_pattern("glider")
        ed.press(LEFT, *px(20, 20))
        ed.release(LEFT, *px(20, 20))
        check("a click stamps the pattern centred on the cell", app.world.population == 5
              and app.world.get_region(19, 19, 3, 3).sum() == 5)
        ed.press(RIGHT, *px(20, 20))
        ed.release(RIGHT, *px(20, 20))
        check("a right click erases it", app.world.population == 0)

        ed.set_tool(editor_module.TOOL_BRUSH)
        ed.press(LEFT, *px(10, 40))
        ed.move(*px(60, 40))                                              # one fast jump
        ed.release(LEFT, *px(60, 40))
        check("a fast brush drag leaves no gaps", app.world.get_region(10, 40, 51, 1).sum() == 51,
              f"{app.world.get_region(10, 40, 51, 1).sum()} of 51")
        ed.undo()
        check("undo takes the whole stroke back", app.world.population == 0)

        app.world.stamp(patterns.get("block"), 127, 63)                    # straddles both seams
        app.camera.x, app.camera.y = 100.0, 40.0                           # the seams are on screen
        ed.set_tool(editor_module.TOOL_SELECT)
        ed.press(LEFT, *px(125, 61))
        ed.move(*px(1, 1))                                                # across both seams
        ed.release(LEFT, *px(1, 1))
        check("a selection dragged across the seams wraps", ed.selection == (125, 61, 5, 5),
              str(ed.selection))
        ed.selection = (127, 63, 2, 2)
        ed.press(LEFT, *px(127, 63))                                      # pick it up
        ed.move(*px(40, 30))
        ed.release(LEFT, *px(40, 30))
        check("dragging a selection moves its cells", app.world.population == 4
              and app.world.get_region(127, 63, 2, 2).sum() == 0
              and app.world.get_region(40, 30, 2, 2).sum() == 4)
        ed.undo()
        check("and undo puts them back", app.world.get_region(127, 63, 2, 2).sum() == 4)

        ed.selection = (127, 63, 2, 2)
        ed.press(LEFT, *px(127, 63))
        ed.move(*px(50, 10))
        ed.press(RIGHT, *px(50, 10))                                      # cancel mid-move
        ed.release(RIGHT, *px(50, 10))
        ed.move(*px(60, 12))
        ed.release(LEFT, *px(60, 12))
        check("right click during a move cancels it", app.world.population == 4
              and app.world.get_region(127, 63, 2, 2).sum() == 4)
        app.camera.x = app.camera.y = 0.0

        ed.copy_selection()
        check("copying puts RLE on the clipboard", "x = 2, y = 2" in clipboard.get("text", ""))
        clipboard["text"] = "x = 3, y = 3\nbo$2bo$3o!"
        ed.hover(px(80, 20))
        ed.paste()
        ed.press(LEFT, *px(80, 20))
        ed.release(LEFT, *px(80, 20))
        check("pasting RLE from elsewhere drops a glider", app.world.population == 9)
        ed.selection = (0, 0, 128, 64)
        ed.transform_selection("rotate")
        check("a rotation that cannot fit is refused, not cropped", app.world.population == 9
              and app.messages and ed.selection == (0, 0, 128, 64))
        ed.selection = (90, 40, 20, 6)
        app.world.put_region(np.ones((6, 20), np.uint8), 90, 40)
        before = app.world.population
        ed.transform_selection("rotate")
        check("rotating keeps every cell and pivots on the centre",
              app.world.population == before and ed.selection == (97, 33, 6, 20)
              and app.world.get_region(97, 33, 6, 20).all(), str(ed.selection))
        ed.set_tool(editor_module.TOOL_PATTERN)
        before = app.camera.zoom
        ed.wheel(120, 200, 100)
        check("a wheel notch zooms in", app.camera.zoom > before)
        ed.press(MIDDLE, 100, 100)
        x0 = app.camera.x
        ed.move(60, 100)
        ed.release(MIDDLE, 60, 100)
        check("a middle drag pans", app.camera.x != x0)

        # A stroke whose unwrapped positions jump by a whole world (the camera
        # wrapped under it) must not paint a stripe from edge to edge.
        app.world.clear()
        ed.set_tool(editor_module.TOOL_BRUSH)
        ed._paint_path([(127, 5), (128 + 128, 5)], erase=False)
        check("a stroke goes the short way round the torus", app.world.population <= 3,
              f"{app.world.population} cells")
        app.world.clear()
        ed.press(LEFT, *px(10, 50))
        zoom = app.camera.zoom
        ed.wheel(120, *px(10, 50))
        check("the wheel does nothing mid-stroke", app.camera.zoom == zoom)
        ed.move_path([px(12, 50), px(14, 50), px(16, 50)])
        ed.release(LEFT, *px(16, 50))
        check("a batch of moves draws one continuous stroke", app.world.get_region(10, 50, 7, 1).sum() == 7)
    finally:
        w.set_clipboard_text, w.clipboard_text = real_set, real_get

    # Undo across a change of world size (a world opened from a file).
    app = FakeApp()
    resized = []
    app.world_resized = lambda: resized.append((app.world.w, app.world.h))
    ed = editor_module.Editor(app)
    app.world.stamp(patterns.get("glider"), 10, 10)
    ed.checkpoint()
    app.world.resize(256, 100)
    ed.undo()
    check("undo across a world resize brings back the old size and cells",
          (app.world.w, app.world.h) == (128, 64) and app.world.population == 5 and resized == [(128, 64)],
          f"{app.world.w}x{app.world.h}, {app.world.population} cells")
    big = World(8192, 2048)
    saved_bound = editor_module.UNDO_BYTES
    editor_module.UNDO_BYTES = 8 * big.bits.nbytes        # low enough that the count limit never bites
    try:
        for _ in range(30):
            ed._undo.append(big.snapshot())
            ed.checkpoint()
        held = sum(s[0].nbytes for s in ed._undo)
        count = len(ed._undo)
    finally:
        editor_module.UNDO_BYTES = saved_bound
    check("undo history is bounded by memory, not only by count",
          held <= 8 * big.bits.nbytes + big.bits.nbytes and count < editor_module.UNDO_DEPTH,
          f"{held / 1e6:.0f} MB in {count} snapshots")
    ed.set_pattern("x:gosperglidergun")
    check("the editor stamps library patterns too", ed.current_art().shape == (9, 36))


# ================================================================== library ===
def test_life105_and_names() -> None:
    pair = rle.parse("#Life 1.05\n#D two gliders\n#P -6 -1\n.*.\n..*\n***\n#P 4 -1\n.*.\n..*\n***\n")
    check("Life 1.05 blocks are placed where their #P lines say", pair.cells.shape == (3, 13)
          and int(pair.cells.sum()) == 10)
    apart = rle.parse("#Life 1.05\n#P 0 0\n*\n#P 0 5\n*\n")
    check("and blocks can be far apart", apart.cells[:, 0].tolist() == [1, 0, 0, 0, 0, 1])
    text = rle.to_rle(pair.cells, name="Glider farm\nwith guns", comment="line one\n\nline two")
    check("a pasted multi-line name stays one line of the file",
          text.splitlines()[0] == "#N Glider farm with guns" and np.array_equal(rle.parse(text).cells, pair.cells))


def make_test_app(width: int = 128, height: int = 64, screen=(512, 256)):
    """A Wallpaper with a real world, camera, editor and look, and no windows at all."""
    import logging
    from golwall.app import Wallpaper
    from golwall.capabilities.persistence import Config
    from golwall.core.look import Look
    from golwall.ui.editor import Editor
    app = Wallpaper.__new__(Wallpaper)
    app.cfg = Config()
    app.log = logging.getLogger("golwall.test")
    app.world = World(width, height)
    app._config_size = (app.world.w, app.world.h)
    app._config_rule = app.world.rule
    app.camera = Camera(screen[0], screen[1], app.world.w, app.world.h, zoom=4)
    app.look = Look(palettes.get("matrix"), 20, 1.6)
    app._palette_index = 0
    app._palette_due = 0.0
    app.gps, app.budget = 8.0, 0.0
    app.world_name, app.message, app.message_serial = "", "", 0
    app.skip_remaining, app._dirty, app.renderer = 0, False, None
    app.panel = type("Panel", (), {"post": lambda self, *a: None})()
    app.editor = Editor(app)
    app._save_world = lambda: None
    return app


def test_apply_world() -> None:
    glider = patterns.get("glider")
    odd = [{"generation": "12,000"}, {"generation": float("inf")}, {"view": [0, 0, None, 32]},
           {"x": 1e999, "y": 3}, {"width": 1e999}, {"gps": 10 ** 400}, {"camera": "nope"},
           {"focus": ["a", 1, 2, 3]}, {"generation": 1e300}]
    bad = []
    for meta in odd:
        app = make_test_app()
        try:
            app.apply_world(glider, "B36/S23", dict(meta, width=256, height=128), "odd")
        except Exception as exc:                      # noqa: BLE001 - that is the failure being tested
            bad.append(f"{meta}: {type(exc).__name__}")
            continue
        if not (app.world_name == "odd" and app.world.rule == "B36/S23" and app.world.population == 5
                and 0 <= app.world.generation <= 2 ** 62):
            bad.append(f"{meta}: half loaded")
    check("odd values in a world file never leave a half-loaded world", not bad, str(bad[:3]))
    app = make_test_app()
    app.apply_world(glider, "B2-a/S12", {}, "strange rule")
    check("an unsupported rule is reported, and the world still loads",
          app.world.rule == "B3/S23" and app.world_name == "strange rule" and "B2" in app.message, app.message)
    app = make_test_app()
    app.world.stamp(patterns.get("block"), 5, 5)
    app.gps = 12.0
    app.apply_world(glider, "", {"width": 640, "height": 360, "gps": 30}, "big one")
    loaded = ((app.world.w, app.world.h), app.world_name, app.gps)
    app.editor.undo()
    check("undoing a world load brings back the old world, its name and its speed",
          loaded == ((640, 360), "big one", 30.0) and (app.world.w, app.world.h) == (128, 64)
          and app.world_name == "" and app.gps == 12.0 and app.world.population == 4, str(loaded))
    app = make_test_app()
    huge = np.zeros((10, 9000), np.uint8)
    huge[0, 0] = huge[0, -1] = 1
    app.apply_world(huge, "", {}, "too wide")
    check("a pattern wider than any world is refused, not cut", (app.world.w, app.world.h) == (128, 64)
          and app.world_name == "" and "9" in app.message, app.message)
    app = make_test_app(1280, 720, screen=(3840, 2160))
    check("a world just big enough is grown until the pattern can be shown whole",
          app._showable(871, 854) == (1920, 1080) and app._showable(3999, 3921) == (7680, 4320))
    app.apply_world(np.ones((854, 871), np.uint8), "", {}, "gun")
    vp = app.camera.viewport()
    check("and the camera then shows all of it", vp.cols >= 871 and vp.rows >= 854, f"{vp}")
    small = make_test_app(1280, 720, screen=(1366, 768))
    check("the clock's whole view cannot fit a 1366-pixel screen, its digits can",
          small._showable(7680, 4320) is None and small._showable(5440, 2432) is not None)


def test_rle_multistate() -> None:
    history = rle.parse("x = 6, y = 1, rule = LifeHistory\nABCDEF!")
    check("LifeHistory: odd states are alive, even ones only mark history",
          history.cells.tolist() == [[1, 0, 1, 0, 1, 0]])
    two_letter = rle.parse("x = 4, y = 1, rule = B3/S23\n2pAA!")
    check("two-letter states are read as one cell each", two_letter.cells.tolist() == [[0, 0, 1, 0]])
    plain = rle.parse("x = 3, y = 1\n3x!")
    check("a plain file's stray letters are still live cells", plain.cells.tolist() == [[1, 1, 1]])
    for text, rule in (("23/3", "B3/S23"), ("B3/S23:T0", "B3/S23"), ("LifeHistory", "B3/S23"), ("/3", "B3/S")):
        try:
            got = normalise_rule(text)
        except ValueError as exc:
            got = str(exc)
        check(f"the rule {text!r} is understood", got == rule, got)


def test_library() -> None:
    import tempfile
    from pathlib import Path
    from golwall.core import library
    started = time.perf_counter()
    lib = library.Library(library.DATA_FILE, None)
    loaded = time.perf_counter() - started
    check("the pattern library loads", not lib.error and len(lib.entries) > 4000,
          f"{len(lib.entries)} patterns in {loaded:.2f}s {lib.error}")
    cats = dict(lib.categories())
    check("every category has patterns", all(cats.get(c) for c in library.CATEGORIES if c != "mine"),
          str({c: len(v) for c, v in cats.items()}))
    check("the classics come first and keep their keys",
          [e.key for e in cats["classic"]] == [p.key for p in patterns.LIBRARY])
    keys = [e.key for e in lib.entries]
    check("keys are unique", len(keys) == len(set(keys)))

    def entry(name):
        return next(e for e in lib.entries if e.name == name and e.category != "classic")

    glider, gun, pulsar = entry("Glider"), entry("Gosper glider gun"), entry("Pulsar")
    check("the simulation found the glider's speed", glider.category == "ship"
          and library.speed_text(glider) == ("c/4", "diagonal") and glider.period == 4)
    check("the Gosper gun is a gun, found by Gosper in 1970", gun.category == "gun" and "Gosper" in gun.by
          and gun.year == 1970)
    check("the pulsar is a period-3 oscillator", pulsar.category == "osc" and pulsar.period == 3)
    rng = np.random.default_rng(7)
    sample = [lib.entries[i] for i in rng.choice(len(lib.entries), 300, replace=False)]
    bad = []
    for e in sample:
        cells = lib.cells(e.key)
        if cells.shape != (e.height, e.width) or int(cells.sum()) != e.population:
            bad.append(e.name)
    check("patterns decode to the size and population on record", not bad, str(bad[:3]))
    big = [e for e in lib.entries if max(e.width, e.height) > lib.preview_size]
    check("big patterns carry a ready-made preview", big and all(lib.preview(e.key) is not None for e in big[:50]))
    hits = lib.search("glider gun")
    check("search finds patterns by every word of their name",
          hits and hits[0].name == "Gosper glider gun", str([h.name for h in hits[:3]]))
    check("search is fast", (lambda t0: (lib.search("p46"), time.perf_counter() - t0)[1] < 0.05)(
        time.perf_counter()))
    with tempfile.TemporaryDirectory() as folder:
        Path(folder, "my glider.rle").write_text("#N Mine\nx = 3, y = 3\nbo$2bo$3o!", encoding="utf-8")
        Path(folder, "junk.rle").write_text("not a pattern at all ~~~", encoding="utf-8")
        mine = library.Library(library.DATA_FILE, Path(folder))
        count = mine.refresh_user()
        users = dict(mine.categories())["mine"]
        check("files in the user's folder become 'My patterns'", count == 1 and users[0].name == "Mine"
              and mine.cells(users[0].key).sum() == 5)
    check("a missing library falls back to the classics",
          len(library.Library(Path("does-not-exist.bin"), None).entries) == len(patterns.LIBRARY))
    check("file names typed by the user are made safe",
          library.safe_filename('a<b>:c/"d"?') == "a_b__c__d__" and library.safe_filename("CON") == "_CON")


def test_library_view_text() -> None:
    from golwall.core import library
    from golwall.ui import library_view, text
    lib = library.get()
    failures = []
    for language in ("pt", "en"):
        text.set_language(language)
        for e in lib.entries:
            try:
                for fn in (library_view.kind_line, library_view.size_line, library_view.credit_line,
                           library_view.note_text, library_view.sources_line, library_view.short_info):
                    fn(e)
            except Exception as exc:                      # noqa: BLE001 - reported below
                failures.append(f"{e.name}: {exc}")
    text.set_language("en")
    check("every pattern gets a tooltip in both languages", not failures, str(failures[:2]))
    notes = sum(1 for e in lib.entries if e.note_pt and e.note_en)
    check("most patterns with a source text have a written note", notes > 1500, f"{notes} notes")
    pulsar = next(e for e in lib.entries if e.name == "Pulsar" and e.category == "osc")
    frames = library_view.animation(pulsar, lib.cells(pulsar.key))
    check("an oscillator animates through its period, in frames of one size",
          len(frames) == 3 and len({f.shape for f in frames}) == 1)
    glider = next(e for e in lib.entries if e.name == "Glider" and e.category == "ship")
    frames = library_view.animation(glider, lib.cells(glider.key))
    check("a spaceship flies on the spot", len(frames) == 4 and len({f.shape for f in frames}) == 1)
    icon = library_view.render_cells(lib.cells(glider.key), 32, (255, 0, 0), tile=(0, 0, 0))
    check("an icon is a square RGBA image with the cells drawn", icon.shape == (32, 32, 4)
          and (icon[..., 0] > 200).sum() > 20)


def test_worlds() -> None:
    import tempfile
    from pathlib import Path
    from golwall.capabilities import persistence, worlds
    nested = "#C golwall-world " + "[" * 20000 + "]" * 20000
    check("a world file with absurdly nested notes is ignored, not a crash",
          worlds.parse_meta_line(nested) is None)
    starters = [w_ for w_ in worlds.list_worlds("pt") if w_.starter]
    check("three example worlds ship with the program", len(starters) == 3,
          str([w_.name for w_ in starters]))
    check("they have Portuguese titles", starters and starters[0].name == "Relógio digital")
    for item in starters:
        pattern, meta = worlds.read(item.path)
        h, w_ = pattern.cells.shape
        fits = (meta.get("width", 0) >= w_ + meta.get("x", 0) and meta.get("height", 0) >= h + meta.get("y", 0)
                and meta["width"] <= 8192 and meta["height"] <= 8192)
        check(f"'{item.name}' fits the world it asks for", fits, f"{w_}x{h} in {meta.get('width')}x{meta.get('height')}")
    clock_meta = worlds.read(starters[0].path)[1]
    check("the clock has empty space around it (it destroys itself on a tight torus)",
          clock_meta["x"] >= 128 and clock_meta["y"] >= 128)
    original = persistence._DATA_DIR
    with tempfile.TemporaryDirectory() as folder:
        persistence._DATA_DIR = Path(folder)
        try:
            world = World(128, 64, seed=5)
            world.seed_soup(0.3)
            path = worlds.path_for('my "world"')
            worlds.write(path, world.to_array(), world.rule, "my world", {"width": 128, "height": 64, "gps": 12})
            listed = [w_ for w_ in worlds.list_worlds() if not w_.starter]
            pattern, meta = worlds.read(path)
            deleted_example = worlds.delete(starters[0].path)
            deleted_mine = worlds.delete(path)
        finally:
            persistence._DATA_DIR = original
    check("a saved world reads back with its notes", np.array_equal(pattern.cells, world.to_array())
          and meta.get("gps") == 12 and listed and listed[0].name == "my _world_")
    check("an example world can never be deleted", not deleted_example and starters[0].path.exists())
    check("a user world can", deleted_mine)


def test_world_persistence_resized() -> None:
    import tempfile
    from pathlib import Path
    from golwall.capabilities import persistence
    big = World(512, 300, seed=9)
    big.seed_soup(0.25)
    original = persistence._DATA_DIR
    with tempfile.TemporaryDirectory() as folder:
        persistence._DATA_DIR = Path(folder)
        try:
            persistence.save_world(big, {"x": 0, "y": 0, "zoom": 1, "config_size": [128, 64]})
            same_config = World(128, 64)
            camera = persistence.load_world(same_config, config_size=(128, 64))
            new_config = World(192, 64)
            refused = persistence.load_world(new_config, config_size=(192, 64))
        finally:
            persistence._DATA_DIR = original
    check("a world opened from a file comes back at its own size after a restart",
          camera is not None and (same_config.w, same_config.h) == (512, 300)
          and np.array_equal(same_config.bits, big.bits))
    check("unless config.json now asks for another size", refused is None and new_config.w == 192)


def test_example_worlds_last() -> None:
    """The parade must never collide: at the common period every ship is back in phase."""
    from golwall.capabilities import worlds
    starters = {item.path.stem: item for item in worlds.list_worlds() if item.starter}
    pattern, meta = worlds.read(starters["3-spaceship-parade"].path)
    world = World(meta["width"], meta["height"])
    world.put_region(pattern.cells, meta.get("x", 0), meta.get("y", 0))
    start = world.population
    counts = []
    for gen in range(1, 841):
        world.step()
        if gen % 420 == 0:
            counts.append(world.population)
    check("the spaceship parade is exactly periodic (nothing collides)", counts == [start, start],
          f"{start} -> {counts}")
    import importlib.util
    spec = importlib.util.spec_from_file_location("make_worlds", ROOT / "tools" / "make_worlds.py")
    make = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(make)
    scene, _, _ = make.garden()
    shipped, gmeta = worlds.read(starters["2-oscillator-garden"].path)
    placed = World(gmeta["width"], gmeta["height"])
    placed.put_region(shipped.cells, gmeta.get("x", 0), gmeta.get("y", 0))
    check("the shipped garden is the one make_worlds builds", np.array_equal(placed.to_array(), scene.cells()))
    check("no two oscillators in the garden ever touch", make.check(scene, 90, 30))


# =================================================================== native ===
def test_window_class_lifetime() -> None:
    """Recreating a window of the same class must not call a freed callback."""
    import ctypes
    from ctypes import wintypes

    from golwall.native import win32 as w
    from golwall.native.window import Window

    seen = []
    msg = wintypes.MSG()

    def pump():
        for _ in range(50):
            if not w.PeekMessageW(ctypes.byref(msg), None, 0, 0, w.PM_REMOVE):
                break
            w.TranslateMessage(ctypes.byref(msg))
            w.DispatchMessageW(ctypes.byref(msg))

    handles = []
    for round_ in range(4):
        win = Window("GolLifetimeTest", "t", w.WS_POPUP, w.WS_EX_TOOLWINDOW,
                     (0, 0, 16, 16), lambda h, m, a, b, r=round_: seen.append(r) or None)
        handles.append(int(win.hwnd))
        pump()
        w.SetWindowPos(win.hwnd, 0, 0, 0, 32, 32, w.SWP_NOACTIVATE | w.SWP_NOSENDCHANGING)
        pump()
        win.destroy()
        pump()
    check("a window class survives being recreated four times", len(set(handles)) == 4)
    check("messages reach the window that is alive now", len(set(seen)) >= 2,
          f"rounds that received messages: {sorted(set(seen))}")


def test_renderer_matches_reference() -> None:
    try:
        from golwall.render.renderer import Box, FrameSpec, Renderer
        renderer = Renderer(128, 64)
    except Exception as exc:                   # no GPU and no WARP: nothing to check
        print(f"skip renderer checks: {exc}")
        return
    from golwall.core.look import Look
    try:
        world = World(128, 64, seed=5)
        world.seed_soup(0.3)
        look = Look(palettes.get("ember"))
        vp = Viewport(120, 60, 0, 0, -2, -1, 5)
        grid = ((1, 0.15), (8, 0.3))
        boxes = (Box((125, 62, 6, 5), (1.0, 0.8, 0.4, 1.0), (1.0, 0.8, 0.4, 0.12)),
                 Box((3, 2, 4, 4), (1.0, 0.4, 0.4, 1.0)))
        renderer.set_mask(patterns.get("glider"), 1)
        spec = FrameSpec(viewport=vp, grid=grid, boxes=boxes, mask_rect=(126, 0, 3, 3),
                         mask_colour=(1.0, 1.0, 0.66, 1.0))
        size, origin = (48, 40), (7, 3)
        renderer.request_reset(1.0)
        renderer.snapshot(world, look, spec, size, origin)
        before = world.to_array()
        world.step()
        after = world.to_array()
        image = renderer.snapshot(world, look, spec, size, origin, dt=0.5)
        age = np.where(after == 1, np.where(before == 1, 2.0, 1.0), 0.0)
        trail = np.where((after == 0) & (before == 1), 1.0, 0.0)
        table = look.table.astype(float) / 255
        worst = 0.0
        for py in range(size[1]):
            for px in range(size[0]):
                lx, ly = px + origin[0] - vp.offset_x, py + origin[1] - vp.offset_y
                cx, cy = (vp.cell_x + lx // vp.zoom) % world.w, (vp.cell_y + ly // vp.zoom) % world.h
                idx = 256 + int(age[cy, cx]) if age[cy, cx] else int(trail[cy, cx] * 255)
                colour = table[idx][[2, 1, 0]]
                for rect, edge, fill in ((b.rect, b.edge, b.fill) for b in boxes[:1]):
                    dx, dy = (cx - rect[0]) % world.w, (cy - rect[1]) % world.h
                    if dx < rect[2] and dy < rect[3]:
                        colour = colour + (np.array(fill[:3]) - colour) * fill[3]
                        if dx in (0, rect[2] - 1) or dy in (0, rect[3] - 1):
                            colour = colour + (np.array(edge[:3]) - colour) * edge[3]
                mx, my = (cx - 126) % world.w, (cy - 0) % world.h
                if mx < 3 and my < 3 and patterns.get("glider")[my, mx]:
                    colour = np.array([1.0, 1.0, 0.66])
                for rect, edge in ((b.rect, b.edge) for b in boxes[1:]):
                    dx, dy = (cx - rect[0]) % world.w, (cy - rect[1]) % world.h
                    if dx < rect[2] and dy < rect[3] and (dx in (0, rect[2] - 1) or dy in (0, rect[3] - 1)):
                        colour = colour + (np.array(edge[:3]) - colour) * edge[3]
                for step, opacity in grid:
                    if lx % vp.zoom == 0 and cx % step == 0:
                        colour = colour + (1 - colour) * opacity
                    if ly % vp.zoom == 0 and cy % step == 0:
                        colour = colour + (1 - colour) * opacity
                got = image[py, px, [2, 1, 0]] / 255.0
                worst = max(worst, float(np.abs(got - colour).max()))
        check("the GPU frame matches the CPU reference", worst < 2.5 / 255, f"worst {worst * 255:.2f}/255")
        probe = renderer.snapshot(world, look, FrameSpec(viewport=vp, probe_colour=(40 / 255, 8 / 255, 32 / 255)),
                                  (72, 72), (0, 0))
        check("probe squares carry the exact marker", tuple(probe[4, 4, :3][::-1]) == (40, 8, 32)
              and tuple(probe[4, 68, :3][::-1]) == (40, 8, 32))
    finally:
        renderer.release()


def main() -> int:
    from golwall.native import win32
    win32.enable_dpi_awareness()
    for name, test in list(globals().items()):
        if name.startswith("test_") and callable(test):
            print(f"\n-- {name[5:]}")
            test()
    # The engine's and the zoom's own suites live beside this one.
    import subprocess
    from pathlib import Path
    for suite in ("test_engine.py", "test_zoom.py"):
        path = Path(__file__).with_name(suite)
        if path.exists():
            print(f"\n-- {suite}")
            done = subprocess.run([sys.executable, str(path)], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
            lines = done.stdout.strip().splitlines()
            print("\n".join(line for line in lines if line.startswith("FAIL")) or f"{len(lines)} lines, no failures")
            check(f"{suite} passes", done.returncode == 0, (lines[-1] if lines else done.stderr[-300:]))
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
