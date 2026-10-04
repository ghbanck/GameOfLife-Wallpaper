"""Render the README hero banner (docs/media/hero.png) from a real Life run.

The look follows the wallpaper's renderer: cells coloured by age through the
palette's birth -> mature -> old ramp, fading trails behind dead cells, and a
three-level grid.  The palette sweeps across the banner from left to right to
show a few of the built-in schemes.

    venv\\Scripts\\python tools\\make_hero.py            # needs Pillow (pip install pillow)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from golwall.core import palettes  # noqa: E402

OUT = ROOT / "docs" / "media" / "hero.png"
W, H = 2560, 1280          # 2x of GitHub's 1280x640 social-preview size
CELL = 10
GW, GH = W // CELL, H // CELL
SWEEP = ("matrix", "deepsea", "ice", "synthwave", "ember")
GENERATIONS = 260
TRAIL_DECAY = 18           # trail value lost per generation (255 -> 0)

GOSPER = """
........................O...........
......................O.O...........
............OO......OO............OO
...........O...O....OO............OO
OO........O.....O...OO..............
OO........O...O.OO....O.O...........
..........O.....O.......O...........
...........O...O....................
............OO......................
"""


def pattern(text: str) -> np.ndarray:
    rows = [r for r in text.strip("\n").splitlines()]
    width = max(len(r) for r in rows)
    return np.array([[c == "O" for c in r.ljust(width, ".")] for r in rows], bool)


def step(grid: np.ndarray) -> np.ndarray:
    n = sum(np.roll(np.roll(grid, dy, 0), dx, 1)
            for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dy, dx) != (0, 0))
    return (n == 3) | (grid & (n == 2))


def simulate(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    grid = np.zeros((GH, GW), bool)
    # A soup over the right two thirds, thinning out towards the title side.
    density = np.clip((np.arange(GW) / GW - 0.40) * 0.9, 0.0, 0.33)
    grid |= rng.random((GH, GW)) < density[None, :]
    # Glider guns firing into the soup from the left.
    gun = pattern(GOSPER)
    for y, x, flip in ((8, 6, False), (GH - 22, 10, True)):
        g = gun[::-1] if flip else gun
        grid[y:y + g.shape[0], x:x + g.shape[1]] = g
    age = np.zeros((GH, GW), np.int32)
    trail = np.zeros((GH, GW), np.int32)
    for _ in range(GENERATIONS):
        nxt = step(grid)
        died = grid & ~nxt
        trail = np.maximum(trail - TRAIL_DECAY, 0)
        trail[died] = 255
        age = np.where(nxt, np.minimum(age + 1, 255), 0)
        grid = nxt
    return grid, age, trail


def palette_columns() -> tuple[np.ndarray, np.ndarray]:
    """Per-column alive/trail LUTs (GW, 256, 3), blended along the sweep."""
    luts = [palettes.build_luts(palettes.get(n)) for n in SWEEP]
    alive = np.empty((GW, 256, 3), np.float32)
    trail = np.empty((GW, 256, 3), np.float32)
    for x in range(GW):
        t = x / (GW - 1) * (len(SWEEP) - 1)
        i = min(int(t), len(SWEEP) - 2)
        f = t - i
        f = f * f * (3 - 2 * f)
        for dst, k in ((alive, 0), (trail, 1)):
            a = luts[i][k][:, 2::-1].astype(np.float32)
            b = luts[i + 1][k][:, 2::-1].astype(np.float32)
            dst[x] = a * (1 - f) + b * f
    return alive, trail


def render(grid, age, trail) -> Image.Image:
    alive_lut, trail_lut = palette_columns()
    cols = np.broadcast_to(np.arange(GW)[None, :], (GH, GW))
    rgb = np.zeros((GH, GW, 3), np.float32)
    rgb = np.where(trail[..., None] > 0, trail_lut[cols, trail], rgb)
    rgb = np.where(grid[..., None], alive_lut[cols, age], rgb)

    # Upscale to cells with a 1px gap, like the renderer's crisp mode.
    big = np.repeat(np.repeat(rgb, CELL, 0), CELL, 1)
    gap = np.zeros((CELL, CELL), bool)
    gap[-1, :] = gap[:, -1] = True
    mask = np.tile(gap, (GH, GW))
    big[mask & grid.repeat(CELL, 0).repeat(CELL, 1)] *= 0.55

    # Three-level grid (1, 8, 64 cells), drawn under the cells.
    yy, xx = np.mgrid[0:H, 0:W]
    lines = np.zeros((H, W), np.float32)
    for every, strength in ((1, 0.035), (8, 0.07), (64, 0.12)):
        step_px = every * CELL
        on = (xx % step_px == 0) | (yy % step_px == 0)
        lines = np.maximum(lines, on * strength)
    empty = big.sum(-1) < 8
    big[empty] += (lines[empty] * 255)[:, None] * np.array([0.75, 0.85, 1.0], np.float32)

    # Darken the left side so the title reads cleanly.
    shade = np.clip((xx / W - 0.12) / 0.46, 0, 1) ** 1.8
    shade = 0.08 + 0.92 * shade
    big *= shade[..., None]
    # Soft vignette.
    cy, cx = (yy / H - 0.5), (xx / W - 0.5)
    big *= (1 - 0.35 * np.clip(cx ** 2 + cy ** 2, 0, 1) * 2)[..., None]

    img = Image.fromarray(np.clip(big, 0, 255).astype(np.uint8), "RGB")
    glow = img.filter(ImageFilter.GaussianBlur(14))
    return Image.blend(img, Image.fromarray(np.maximum(np.asarray(img), np.asarray(glow))), 0.6)


def font(names: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont:
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def typeset(img: Image.Image) -> Image.Image:
    draw = ImageDraw.Draw(img)
    title = font(("seguisb.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf"), 150)
    sub = font(("segoeui.ttf", "DejaVuSans.ttf"), 54)
    mono = font(("CascadiaMono.ttf", "consola.ttf", "DejaVuSansMono.ttf"), 36)
    x, y = 150, 380
    draw.text((x, y), "Game of Life", font=title, fill=(240, 246, 242))
    draw.text((x, y + 175), "Wallpaper", font=title, fill=(80, 225, 120))
    draw.text((x + 4, y + 395), "Conway's Game of Life as a live, drawable", font=sub, fill=(190, 200, 205))
    draw.text((x + 4, y + 465), "wallpaper for Windows 10 & 11.", font=sub, fill=(190, 200, 205))
    tags = "GPU-rendered  ·  behind your icons  ·  4,800 patterns"
    draw.text((x + 6, y + 585), tags, font=mono, fill=(110, 130, 140))
    return img


def main() -> int:
    rng = np.random.default_rng(23)
    img = typeset(render(*simulate(rng)))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, optimize=True)
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
