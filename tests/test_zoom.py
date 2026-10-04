"""Self-checks for zooming out below one screen pixel per cell.

Run with ``venv\\Scripts\\python tests\\test_zoom.py``.  The camera is checked on
its own -- levels, limits, framing, coordinate round trips, saved state -- and
the renderer is checked offscreen at 2 and 3 cells per pixel against a CPU
reference of the block rule: a pixel takes the brightest colour of its block,
and every overlay counts as hit when any cell of the block is.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))      # runnable as a script from any folder

from golwall.core import palettes, patterns
from golwall.core.camera import SHRINKS, Camera, Viewport
from golwall.core.life import World

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "ok  " if condition else "FAIL"
    print(f"{status} {name}" + (f"   {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def label(zoom: float) -> str:
    return f"1/{round(1 / zoom)}" if zoom < 1 else str(zoom)


# ==================================================================== camera ===
def test_levels() -> None:
    levels = Camera.LEVELS
    check("the levels run from 1/16 to 64, smallest first",
          levels[0] == 1 / 16 and levels[-1] == 64 and list(levels) == sorted(levels))
    check("zoomed-out levels are 1/k for k in 2, 3, 4, 6, 8, 12, 16",
          [round(1 / z) for z in levels if z < 1] == sorted(SHRINKS, reverse=True))
    check("whole levels are every int from 1 to 64",
          [z for z in levels if z >= 1] == list(range(1, 65))
          and all(isinstance(z, int) for z in levels if z >= 1))

    cam = Camera(1920, 1080, 7680, 8640)                     # down to 1/4 allowed
    snaps = {0.3: 1 / 3, 0.45: 1 / 2, 0.26: 1 / 4, 0.1: 1 / 4, 0.0: 1 / 4, -5: 1 / 4,
             float("nan"): 1 / 4, 0.9: 1, 2.0: 2, 2.2: 2, 7: 7, 100: 64, float("inf"): 64}
    wrong = {z: cam.clamp_zoom(z) for z, want in snaps.items() if cam.clamp_zoom(z) != want}
    check("any value snaps to the nearest allowed level", not wrong, str(wrong))
    check("a whole zoom comes back as an int", isinstance(cam.clamp_zoom(2.0), int))
    cam.set_zoom(0.5)
    vp = cam.viewport()
    check("zoomed out the camera reports its shrink",
          cam.shrink == 2 and vp.shrink == 2 and vp.zoom == 1 and vp.scale == 0.5)
    cam.set_zoom(5)
    check("zoomed in the shrink is 1", cam.shrink == 1 and cam.viewport().shrink == 1
          and cam.viewport().scale == 5)


def test_zoom_by() -> None:
    cam = Camera(320, 180, 7680, 8640, zoom=1)               # 1/16 fits: 320*16 <= 7680
    seen = [cam.zoom]
    while cam.zoom_by(-1):
        seen.append(cam.zoom)
    check("out from 1 the notches go 1/2, 1/3, 1/4, 1/6, 1/8, 1/12, 1/16",
          [label(z) for z in seen] == ["1", "1/2", "1/3", "1/4", "1/6", "1/8", "1/12", "1/16"],
          " ".join(label(z) for z in seen))
    seen = [cam.zoom]
    while cam.zoom_by(1):
        seen.append(cam.zoom)
    check("and in again they climb back and keep today's ladder",
          [label(z) for z in seen] == ["1/16", "1/12", "1/8", "1/6", "1/4", "1/3", "1/2",
                                       "1", "2", "3", "4", "8", "16", "32", "64"],
          " ".join(label(z) for z in seen))
    check("back at 1 the zoom is an int again", isinstance(Camera(320, 180, 7680, 8640, zoom=0.5).clamp_zoom(1.0), int))

    def old_notch(zoom: int, steps: int) -> int:           # the pre-zoom-out rule
        if zoom < 4 or (steps < 0 and zoom <= 4):
            target = zoom + steps
        else:
            target = zoom * (2 ** steps)
        return max(1, min(64, int(round(target))))

    bad = []
    for zoom in range(2, 65):
        for steps in (1, -1):
            cam.set_zoom(zoom)
            cam.zoom_by(steps)
            if cam.zoom != old_notch(zoom, steps):
                bad.append((zoom, steps, cam.zoom))
    check("from 2 px per cell up, one notch does exactly what it used to", not bad, str(bad[:3]))

    cam.set_zoom(1)
    cam.zoom_by(-3)
    out3 = cam.zoom
    cam.zoom_by(3)
    back = cam.zoom
    cam.set_zoom(2)
    cam.zoom_by(-2)
    check("several notches at once walk the same ladder", out3 == 1 / 4 and back == 1 and cam.zoom == 1 / 2,
          f"{label(out3)}, {label(back)}, {label(cam.zoom)}")
    small = Camera(3840, 2160, 1280, 720, zoom=4)
    walked = [small.zoom]
    while small.zoom_by(-1):
        walked.append(small.zoom)
    check("where the world is small, zooming out goes past the fit (the torus repeats) down to 1/2",
          walked == [4, 3, 2, 1, 1 / 2], str([label(z) for z in walked]))
    small.fit()
    check("and fit comes back to the zoom that shows it once", small.zoom == 3)


def test_min_zoom() -> None:
    check("a 7680x8640 world on 3840x2160 fits at 1/2",
          Camera(3840, 2160, 7680, 8640).fit_zoom() == 1 / 2)
    check("and on 1920x1080 at 1/4", Camera(1920, 1080, 7680, 8640).fit_zoom() == 1 / 4)
    check("4K still fits a 1280x720 world at 3 px per cell",
          Camera(3840, 2160, 1280, 720).fit_zoom() == 3)
    check("a world the size of the screen fits at 1", Camera(1920, 1080, 1920, 1080).fit_zoom() == 1)
    check("one axis short is enough to refuse 1/2 as a fit", Camera(1920, 1080, 3840, 2000).fit_zoom() == 1)
    check("past the fit a small world goes out to 8 copies across",
          Camera(3840, 2160, 1280, 720).min_zoom() == 1 / 2 and Camera(3840, 2160, 640, 360).min_zoom() == 1)
    check("but never finer than 1/4, however big the world",
          Camera(3840, 2160, 7680, 8640).min_zoom() == 1 / 4 and Camera(320, 180, 8192, 8192).min_zoom() == 1 / 16)
    bad = []
    for screen in [(3840, 2160), (1920, 1080), (1366, 768), (320, 180)]:
        for world in [(7680, 8640), (4096, 4096), (8192, 2304), (1280, 720)]:
            cam = Camera(*screen, *world)
            for zoom in [z for z in Camera.LEVELS if cam.fit_zoom() <= z <= 4]:
                cam.set_zoom(zoom)
                cam.x, cam.y = 10.7, 20.3
                vp = cam.viewport()
                shown_w = screen[0] * vp.shrink if zoom < 1 else screen[0] / zoom
                shown_h = screen[1] * vp.shrink if zoom < 1 else screen[1] / zoom
                if shown_w > world[0] or shown_h > world[1] or vp.cols > world[0] or vp.rows > world[1]:
                    bad.append((screen, world, label(zoom)))
    check("no level from the fit in ever shows more than the world", not bad, str(bad[:2]))


def test_frame() -> None:
    cam = Camera(3840, 2160, 7680, 8640, zoom=8)
    version = cam.version
    result = cam.frame((0, 5200, 7680, 2240))
    check("framing a 7680x2240 band on 4K picks 1/2", cam.zoom == 1 / 2 and result is None, label(cam.zoom))
    check("framing moves the camera", cam.version > version)
    check("and centres the band", cam.world_to_screen(3840, 6320) == (1920, 1080),
          str(cam.world_to_screen(3840, 6320)))
    check("its corners land on screen",
          cam.world_to_screen(0, 5200) == (0, 520) and cam.world_to_screen(7679, 7439) == (3839, 1639),
          f"{cam.world_to_screen(0, 5200)} {cam.world_to_screen(7679, 7439)}")
    vp = cam.viewport()
    check("the viewport starts at the band's left edge", (vp.cell_x, vp.cell_y) == (0, 4160), str(vp.rect))
    cam.frame((0, 0, 1000, 500))
    check("a rectangle that fits zoomed in gets the largest whole level", cam.zoom == 3, label(cam.zoom))
    cam.frame((100, 100, 3, 2))
    check("a tiny one stops at the maximum", cam.zoom == Camera.MAX_ZOOM)
    cam.frame((0, 0, 20000, 100))
    check("one bigger than any level falls back to the smallest", cam.zoom == cam.min_zoom())
    cam.frame((7600, 8600, 200, 100))
    check("a rectangle across the seams is centred across them",
          cam.world_to_screen(20, 10) == (1920, 1080), str(cam.world_to_screen(20, 10)))

    # Zoomed out the view starts on a block boundary: a screen-wide rectangle
    # off that grid must not lose its last column to the snap.
    hd = Camera(1920, 1080, 7680, 8640)
    offgrid = []
    for rect in ((4773, 3981, 3840, 855), (3926, 1214, 3839, 936), (7679, 8639, 3838, 2159), (1, 1, 1920, 1080)):
        hd.frame(rect)
        x, y, w, h = rect
        corners = [hd.world_to_screen((x + dx) % 7680, (y + dy) % 8640) for dx in (0, w - 1) for dy in (0, h - 1)]
        if not all(0 <= sx < 1920 and 0 <= sy < 1080 for sx, sy in corners):
            offgrid.append((rect, label(hd.zoom), corners))
    check("a zoomed-out frame keeps a rectangle off the block grid wholly on screen", not offgrid, str(offgrid[:2]))
    hd.frame((4773, 3981, 3840, 855))
    check("and takes the next level out when the snap leaves no room", hd.zoom == 1 / 3, label(hd.zoom))
    hd.frame((4772, 3981, 3840, 855))
    check("while an aligned one keeps 1/2, flush with the screen",
          hd.zoom == 1 / 2 and hd.world_to_screen(4772, 3981)[0] == 0, label(hd.zoom))
    small = Camera(1920, 1080, 1280, 720)
    small.frame((0, 0, 400, 300))
    check("framing what fits never zooms out past the fit", small.zoom == 3)
    small.frame((0, 0, 1280, 720))
    check("framing a whole world bigger than the fit goes past it to show it all",
          small.zoom == 1 and small.fit_zoom() == 2, label(small.zoom))


def test_coordinates() -> None:
    rng = np.random.default_rng(1)
    for zoom, k in ((1 / 2, 2), (1 / 3, 3)):
        cam = Camera(1920, 1080, 7680, 8640, zoom=zoom)
        bad_px, bad_cell, bad_vp, bad_snap = [], [], [], []
        for _ in range(12):
            cam.x, cam.y = rng.random() * cam.world_w, rng.random() * cam.world_h
            vp = cam.viewport()
            if vp.cell_x % k or vp.cell_y % k or vp.offset_x or vp.offset_y or vp.zoom != 1:
                bad_snap.append(vp)
            for sx, sy in zip(rng.integers(0, 1920, 50), rng.integers(0, 1080, 50)):
                sx, sy = int(sx), int(sy)
                cell = cam.screen_to_world(sx, sy)
                if cam.world_to_screen(*cell) != (sx, sy):
                    bad_px.append((sx, sy, cell))
                if cell != ((vp.cell_x + sx * k) % cam.world_w, (vp.cell_y + sy * k) % cam.world_h):
                    bad_vp.append((sx, sy, cell))
            for cx, cy in zip(rng.integers(0, 7680, 50), rng.integers(0, 8640, 50)):
                cx, cy = int(cx), int(cy)
                sx, sy = cam.world_to_screen(cx, cy)
                if not (0 <= sx < 1920 and 0 <= sy < 1080):
                    continue
                bx, by = cam.screen_to_world(sx, sy)
                if (cx - bx) % cam.world_w >= k or (cy - by) % cam.world_h >= k:
                    bad_cell.append((cx, cy, bx, by))
        name = label(zoom)
        check(f"at {name} the top-left snaps to a multiple of {k}", not bad_snap, str(bad_snap[:1]))
        check(f"at {name} pixel -> cell -> pixel comes back", not bad_px, str(bad_px[:2]))
        check(f"at {name} cell -> pixel -> cell stays in the cell's block", not bad_cell, str(bad_cell[:2]))
        check(f"at {name} the pointer picks the block the renderer draws there", not bad_vp, str(bad_vp[:2]))
        check(f"at {name} unwrapped cells wrap to the same cell",
              all(tuple(v % m for v, m in zip(cam.unwrapped(sx, sy), (cam.world_w, cam.world_h)))
                  == cam.screen_to_world(sx, sy) for sx, sy in ((0, 0), (1919, 1079), (700, 3))))

    cam = Camera(1920, 1080, 7680, 8640, zoom=1 / 3)
    cam.x, cam.y = 300.4, 90.0
    starts = []
    for _ in range(5):
        starts.append(cam.viewport().cell_x)
        cam.pan_pixels(-1, 0)
    check("a one-pixel pan at 1/3 moves the view by exactly one block",
          [b - a for a, b in zip(starts, starts[1:])] == [-3] * 4, str(starts))

    cam = Camera(1920, 1080, 7680, 8640, zoom=1)
    cam.x, cam.y = 1000.25, 2000.75
    anchor = (700, 400)
    before = cam.screen_to_world(*anchor)
    x0, y0 = cam.x, cam.y
    cam.set_zoom(1 / 2, anchor)
    check("zooming out keeps the cell under the pointer in the pointer's pixel",
          cam.world_to_screen(*before) == anchor, f"{cam.world_to_screen(*before)} vs {anchor}")
    block = cam.screen_to_world(*anchor)
    cam.set_zoom(8, anchor)
    after = cam.screen_to_world(*anchor)
    check("zooming in from 1/2 lands inside the block that was under the pointer",
          0 <= (after[0] - block[0]) % cam.world_w < 2 and 0 <= (after[1] - block[1]) % cam.world_h < 2,
          f"{block} -> {after}")
    cam.set_zoom(1, anchor)
    check("out and back in at one spot returns exactly", math.isclose(cam.x, x0) and math.isclose(cam.y, y0),
          f"({cam.x}, {cam.y}) vs ({x0}, {y0})")
    cam.set_zoom(1 / 4)
    cam.center()
    vp = cam.viewport()
    check("centring at 1/4 puts the middle of the world mid-screen",
          abs(cam.world_to_screen(3840, 4320)[0] - 960) <= 1 and abs(cam.world_to_screen(3840, 4320)[1] - 540) <= 1
          and (vp.cols, vp.rows) == (7680, 4320), str(cam.world_to_screen(3840, 4320)))
    cam.fit()
    check("fit goes to the smallest allowed level", cam.zoom == 1 / 4)


def test_state() -> None:
    cam = Camera(3840, 2160, 7680, 8640)
    cam.restore({"x": 10.5, "y": 20.0, "zoom": 3})
    check("an old saved state with an int zoom still restores",
          cam.zoom == 3 and isinstance(cam.zoom, int) and (cam.x, cam.y) == (10.5, 20.0))
    cam.set_zoom(1 / 2)
    state = cam.state()
    other = Camera(3840, 2160, 7680, 8640)
    other.restore(json.loads(json.dumps(state)))
    check("a zoomed-out state round-trips through JSON", other.state() == state and other.zoom == 1 / 2,
          str(state))
    wide = Camera(1920, 1080, 7680, 8640)
    wide.restore({"x": 0, "y": 0, "zoom": 0.3333})
    check("a rounded 1/3 snaps back to 1/3", wide.zoom == 1 / 3)
    small = Camera(3840, 2160, 1280, 720)
    small.restore(state)
    check("a zoomed-out state on a small world is kept (the world repeats)", small.zoom == 1 / 2)
    small.restore({"x": 0, "y": 0, "zoom": 1 / 8})
    check("one further out than that world allows clamps to its limit", small.zoom == 1 / 2)
    before, version = other.state(), other.version
    other.restore({"x": 1, "y": 2})
    other.restore({"x": 1, "y": 2, "zoom": "wide"})
    other.restore({"x": 1, "y": 2, "zoom": 10 ** 400})       # float() overflows: no crash
    other.restore({"x": "left", "y": 2, "zoom": 4})          # no half-restored zoom either
    other.restore({"x": float("nan"), "y": 2, "zoom": 4})
    check("a broken state changes nothing", other.state() == before and other.version == version)


def test_grid_zoomed_out() -> None:
    from golwall.core.grid import Grid
    grid = Grid(levels=(1, 8, 64))
    check("grid opacity takes a fractional zoom", grid.opacity_for(64, 0.5) > 0 and grid.opacity_for(8, 0.5) == 0
          and grid.opacity_for(1, 1 / 16) == 0)
    bad = []
    for k in (2, 3, 4):
        cam = Camera(640, 360, 7680, 8640, zoom=1 / k)
        cam.x, cam.y = 1234.5, 777.0
        vp = cam.viewport()
        lines = list(grid.lines(vp, 640, 360))
        for step in grid.levels:
            opacity = grid.opacity_for(step, 1 / k)
            want = set()
            if opacity > 0 and step >= 2 * k:
                want = {p for p in range(640) if any((vp.cell_x + p * k + t) % step == 0 for t in range(k))}
            got = {line.position for line in lines if line.vertical and line.opacity == opacity}
            if opacity > 0 and got != want:
                bad.append((k, step, sorted(got ^ want)[:4]))
    check("zoomed out, grid lines fall on the pixels whose block holds a multiple", not bad, str(bad[:2]))


# ================================================================== renderer ===
def reference_frame(world, look, before, after, vp, size, origin, boxes, mask, mask_rect, mask_colour, grid):
    """The block rule on the CPU: brightest colour of the block, overlays hit by any cell."""
    age = np.where(after == 1, np.where(before == 1, 2.0, 1.0), 0.0)
    trail = np.where((after == 0) & (before == 1), 1.0, 0.0)
    table = look.table.astype(float) / 255
    index = np.where(age > 0, 256 + age.astype(int), (trail * 255).astype(int))
    colours = table[index][..., [2, 1, 0]]                 # (h, w, rgb)
    k, W, H = vp.shrink, world.w, world.h
    out = np.zeros((size[1], size[0], 3))

    def hits(start, span_at, length, modulus):
        rel = [(start + t - span_at) % modulus for t in range(k)]
        inside = [d for d in rel if d < length]
        return bool(inside), any(d in (0, length - 1) for d in inside)

    def box(colour, xs0, ys0, item):
        rx, ry, rw, rh = item.rect
        in_x, edge_x = hits(xs0, rx, rw, W)
        in_y, edge_y = hits(ys0, ry, rh, H)
        if rw <= 0 or rh <= 0 or not (in_x and in_y):
            return colour
        if item.fill[3] > 0:
            colour = colour + (np.array(item.fill[:3]) - colour) * item.fill[3]
        if edge_x or edge_y:
            colour = colour + (np.array(item.edge[:3]) - colour) * item.edge[3]
        return colour

    for py in range(size[1]):
        for px in range(size[0]):
            lx, ly = px + origin[0] - vp.offset_x, py + origin[1] - vp.offset_y
            x0, y0 = (vp.cell_x + lx * k) % W, (vp.cell_y + ly * k) % H
            xs = [(x0 + t) % W for t in range(k)]
            ys = [(y0 + t) % H for t in range(k)]
            colour = colours[np.ix_(ys, xs)].max(axis=(0, 1))
            colour = box(colour, x0, y0, boxes[0])
            lit = any(mask[my, mx] for my in ((y - mask_rect[1]) % H for y in ys) if my < mask_rect[3]
                      for mx in ((x - mask_rect[0]) % W for x in xs) if mx < mask_rect[2])
            if lit:
                colour = colour + (np.array(mask_colour[:3]) - colour) * mask_colour[3]
            colour = box(colour, x0, y0, boxes[1])
            for step, opacity in grid:
                if step < 2 * k:
                    continue
                # The block holds a cell that carries this level's line at 1 px per cell --
                # cell 0 too, where the block runs over a seam the step does not divide.
                if any(x % step == 0 for x in xs):
                    colour = colour + (1 - colour) * opacity
                if any(y % step == 0 for y in ys):
                    colour = colour + (1 - colour) * opacity
            out[py, px] = colour
    return out


def test_renderer_zoomed_out() -> None:
    try:
        from golwall.render.renderer import Box, FrameSpec, Renderer
        renderer = Renderer(128, 80)
    except Exception as exc:                   # no GPU and no WARP: nothing to check
        print(f"skip renderer checks: {exc}")
        return
    from golwall.core.look import Look
    try:
        # 128 and 80 are not multiples of 3: blocks straddle the seams.  80 is no
        # multiple of 64 either, so at 1/3 the block (78, 79, 0) must still carry row 0's line.
        world = World(128, 80, seed=5)
        world.seed_soup(0.3)
        look = Look(palettes.get("ember"))
        glider = patterns.get("glider")
        renderer.set_mask(glider, 1)
        mask_rect, mask_colour = (126, 79, 3, 3), (1.0, 1.0, 0.66, 0.8)
        grid = ((1, 0.15), (2, 0.2), (8, 0.3), (64, 0.5))
        boxes = (Box((125, 77, 7, 5), (1.0, 0.8, 0.4, 1.0), (1.0, 0.8, 0.4, 0.12)),
                 Box((40, 30, 1, 1), (1.0, 0.4, 0.4, 1.0)))
        size, origin = (48, 40), (5, 3)
        for k in (2, 3):
            vp = Viewport(118, 74, min(128, 48 * k), min(80, 40 * k), 0, 0, 1, k)
            spec = FrameSpec(viewport=vp, grid=grid, boxes=boxes, mask_rect=mask_rect, mask_colour=mask_colour)
            renderer.request_reset(1.0)
            renderer.snapshot(world, look, spec, size, origin)
            before = world.to_array()
            world.step()
            after = world.to_array()
            image = renderer.snapshot(world, look, spec, size, origin, dt=0.5)
            want = reference_frame(world, look, before, after, vp, size, origin, boxes, glider,
                                   mask_rect, mask_colour, grid)
            got = image[..., [2, 1, 0]] / 255.0
            worst = float(np.abs(got - want).max())
            check(f"at {k} cells per pixel the GPU frame matches the block reference", worst < 2.5 / 255,
                  f"worst {worst * 255:.2f}/255")
            smooth = renderer.snapshot(world, look, FrameSpec(viewport=vp, smooth=True), size, origin)
            plain = renderer.snapshot(world, look, FrameSpec(viewport=vp), size, origin)
            check(f"at {k} cells per pixel smooth mode is ignored", np.array_equal(smooth, plain))

        lone = World(128, 80)
        lone.put_region(np.ones((1, 1), np.uint8), 61, 37)
        vp = Viewport(0, 0, 128, 80, 0, 0, 1, 4)
        renderer.request_reset(1.0)
        image = renderer.snapshot(lone, look, FrameSpec(viewport=vp), (32, 20))
        lit = np.argwhere(image[..., :3].max(axis=2) > 0)
        check("a lone live cell still lights exactly its pixel at 1/4",
              lit.tolist() == [[37 // 4, 61 // 4]], str(lit.tolist()[:3]))
    finally:
        renderer.release()


def main() -> int:
    for name, test in list(globals().items()):
        if name.startswith("test_") and callable(test):
            print(f"\n-- {name[5:]}")
            test()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
