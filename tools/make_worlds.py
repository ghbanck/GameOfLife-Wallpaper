"""Build the example worlds that ship in golwall/data/worlds.

    venv\\Scripts\\python tools\\make_worlds.py            # write them
    venv\\Scripts\\python tools\\make_worlds.py --check    # and prove they last

Three worlds, each meant to run forever without anything added to it:

1. The digital clock -- Vladan Majerech's clockMini, a reduction of the clock
   'dim' built for the Code Golf challenge "Build a digital clock in Conway's
   Game of Life".  It needs empty space around it: on a torus the size of its
   own bounding box the right edge of the display touches the AM label
   across the seam, and the two destroy each other within 6000 generations.
2. An oscillator garden: several dozen oscillators from the library, spaced so
   they never touch, under a title written in blocks.
3. A spaceship parade: twelve lanes of orthogonal spaceships at seven different
   speeds, going round the torus in both directions.  One kind of ship per
   lane, lanes far enough apart that no two ships can ever meet.

``--check`` runs each garden and parade against the sum of its parts (every
object on its own) and fails if a single cell differs -- which is what any
interaction between two objects would cause.  Nothing is written unless the
proof passes.

The clock's minute is 2880 generations: measured by the minute digit
repeating every 28,800 generations (ten minutes) and at no other lag.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from golwall.core import library, rle  # noqa: E402
from golwall.core.life import World  # noqa: E402

OUT = ROOT / "golwall" / "data" / "worlds"
LIB = library.Library(library.DATA_FILE, None)


def find(name: str):
    wanted = name.lower()
    for entry in LIB.entries:
        if entry.category != "classic" and (entry.name.lower() == wanted
                                            or wanted in (a.lower() for a in entry.aliases)):
            return entry
    raise KeyError(name)


# -- a tiny plane simulator (no torus), for orienting and measuring objects ----------
def _crop(c):
    rows = np.flatnonzero(c.any(axis=1))
    if not rows.size:
        return c[:0, :0], 0, 0
    cols = np.flatnonzero(c.any(axis=0))
    return c[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1], int(cols[0]), int(rows[0])


def _step(c):
    a = np.pad(c, 2)
    n = (a[:-2, :-2] + a[:-2, 1:-1] + a[:-2, 2:] + a[1:-1, :-2] + a[1:-1, 2:]
         + a[2:, :-2] + a[2:, 1:-1] + a[2:, 2:])
    return ((n == 3) | ((n == 2) & (a[1:-1, 1:-1] == 1))).astype(np.uint8)


def run(cells, generations):
    """Frames with their offsets: [(cells, x, y), ...] for generations 0..n."""
    out, c, x, y = [], cells, 0, 0
    for _ in range(generations + 1):
        out.append((c, x, y))
        c, dx, dy = _crop(_step(c))
        x += dx - 1
        y += dy - 1
    return out


def envelope(cells, period):
    """The box every phase of an oscillator stays inside, relative to the cells' origin."""
    frames = run(cells, period)
    x0 = min(x for _, x, _ in frames)
    y0 = min(y for _, _, y in frames)
    x1 = max(x + c.shape[1] for c, x, _ in frames)
    y1 = max(y + c.shape[0] for c, _, y in frames)
    return x0, y0, x1 - x0, y1 - y0


def heading_right(cells, period):
    """The rotation of a spaceship that flies towards +x."""
    for k in range(4):
        art = np.ascontiguousarray(np.rot90(cells, k))
        frames = run(art, period)
        c0, x0, y0 = frames[0]
        c1, x1, y1 = frames[-1]
        if x1 > x0 and y1 == y0 and np.array_equal(c0, c1):
            return art
    raise ValueError("not an orthogonal spaceship")


# -- block lettering ---------------------------------------------------------------
FONT = {
    "J": ["..###", "...#.", "...#.", "...#.", "#..#.", "#..#.", ".##.."],
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "G": [".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".###."],
    "D": ["####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."],
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "V": ["#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."],
    "I": ["###", ".#.", ".#.", ".#.", ".#.", ".#.", "###"],
    " ": ["...", "...", "...", "...", "...", "...", "..."],
}
PITCH = 4                        # a 2x2 block every 4 cells: a still life, however they sit


def lettering(text: str) -> np.ndarray:
    columns = []
    for ch in text:
        glyph = FONT[ch]
        columns.append(np.array([[1 if c == "#" else 0 for c in row] for row in glyph], np.uint8))
        columns.append(np.zeros((7, 1), np.uint8))
    bitmap = np.hstack(columns[:-1])
    h, w = bitmap.shape
    out = np.zeros((h * PITCH, w * PITCH), np.uint8)
    for y, x in zip(*np.nonzero(bitmap)):
        out[y * PITCH:y * PITCH + 2, x * PITCH:x * PITCH + 2] = 1
    return rle.crop(out)


# -- the worlds ---------------------------------------------------------------------
class Scene:
    def __init__(self, width: int, height: int) -> None:
        self.w, self.h = width, height
        self.parts: list[tuple[np.ndarray, int, int]] = []

    def put(self, cells: np.ndarray, x: int, y: int) -> None:
        self.parts.append((np.ascontiguousarray(cells, np.uint8), int(x) % self.w, int(y) % self.h))

    def cells(self, parts=None) -> np.ndarray:
        world = World(self.w, self.h)
        for c, x, y in (self.parts if parts is None else parts):
            world.put_region(c, x, y, "or")
        return world.to_array()


GARDEN = ["Blinker", "Toad", "Beacon", "Clock", "Pulsar", "Pentadecathlon", "Figure eight", "Kok's galaxy",
          "101", "Queen bee shuttle", "Twin bees shuttle", "Tumbler", "Octagon 2", "Mold", "Mazing",
          "Pinwheel", "Unix", "Cross", "Caterer", "Jam", "Fumarole", "Hertz oscillator", "Achim's p16",
          "Roteightor", "Monogram", "Blocker", "Negentropy", "Karel's p15", "Coe's p8", "Pentoad", "Fountain",
          "Worker bee", "Trans-queen bee shuttle", "Heavyweight emulator", "Middleweight volcano", "Achim's p4",
          "Achim's p8", "Gray counter", "Airforce", "Great on-off", "Spark coil", "Dinner table",
          "Mini pressure cooker", "Smiley", "Star", "Why not", "Phoenix 1", "Quad", "Eureka", "Cuphook",
          "Muttering moat 1", "Jolson", "Tanner's p46", "p60 glider shuttle", "Silver's p5", "Snacker",
          "Bipole", "Tripole", "Elevener", "Candelabra", "Test tube baby", "Laputa", "Baker's dozen",
          "Coe's p8", "Hustler", "Cloverleaf", "Mathematician", "Glasses", "By flops", "Hectic",
          "Beehive on dock", "Wavefront", "Butterfly"]


def garden() -> tuple[Scene, dict, list[str]]:
    scene = Scene(640, 360)                   # 6 pixels per cell on a 4K screen
    title = lettering("JOGO DA VIDA")
    scene.put(title, (scene.w - title.shape[1]) // 2, 22)
    picked, seen = [], set()
    for name in GARDEN:
        try:
            entry = find(name)
        except KeyError:
            continue
        if entry.key in seen or entry.moves or entry.period < 2 or entry.kind != "periodic" or entry.lifespan:
            continue
        cells = LIB.cells(entry.key)
        if max(cells.shape) > 60:
            continue
        seen.add(entry.key)
        picked.append((entry, cells, envelope(cells, entry.period)))
    # Rows of similar height, spread evenly over the space under the title,
    # each row's oscillators spread evenly across the width.
    picked.sort(key=lambda p: (p[2][3], p[2][2]))
    top, bottom, margin, min_gap = 22 + title.shape[0] + 24, scene.h - 16, 24, 10
    usable = scene.w - 2 * margin
    rows, row, width = [], [], 0
    per_row = math.ceil(len(picked) / 6)                   # about six rows
    for item in picked:
        w_ = item[2][2]
        if row and (len(row) >= per_row or width + min_gap + w_ > usable):
            rows.append(row)
            row, width = [], 0
        width += (min_gap if row else 0) + w_
        row.append(item)
    if row:
        rows.append(row)
    heights = [max(i[2][3] for i in r) for r in rows]
    spare = (bottom - top) - sum(heights)
    if spare < min_gap * (len(rows) - 1):
        raise ValueError("the garden does not fit")
    gap_y = spare / len(rows)
    y = top + gap_y / 2
    placed = []
    for r, height in zip(rows, heights):
        widths = [i[2][2] for i in r]
        gap_x = (usable - sum(widths)) / len(r)
        x = margin + gap_x / 2
        for entry, cells, (ex, ey, ew, eh) in r:
            oy = int(round(y + (height - eh) / 2))
            scene.put(cells, int(round(x)) - ex, oy - ey)
            placed.append(entry.name)
            x += ew + gap_x
        y += height + gap_y
    meta = {"title": {"en": "Oscillator garden", "pt": "Jardim de osciladores"},
            "width": scene.w, "height": scene.h, "view": [0, 0, scene.w, scene.h], "gps": 6,
            "palette": "synthwave", "x": 0, "y": 0}
    notes = [f"{len(placed)} oscillators from the Life Lexicon and LifeWiki collections:",
             ", ".join(placed)]
    return scene, meta, notes


PARADE = [("LWSS", 6, 6), ("Weekender", 3, 10), ("Copperhead", 5, 8), ("MWSS", 6, 7), ("Loafer", 5, 8),
          ("30P5H2V0", 4, 8), ("Spider", 3, 8), ("HWSS", 6, 7), ("Dart", 4, 8), ("Schick engine", 3, 8),
          ("Turtle", 4, 8), ("Hammerhead", 3, 8)]


def parade() -> tuple[Scene, dict, list[str]]:
    scene = Scene(960, 540)                   # 4 pixels per cell on a 4K screen
    lanes = []
    for name, copies, gap in PARADE:
        try:
            entry = find(name)
        except KeyError:
            continue
        cells = heading_right(LIB.cells(entry.key), entry.period)
        frames = run(cells, entry.period)
        height = max(y + c.shape[0] for c, _, y in frames) - min(y for _, _, y in frames)
        lanes.append((entry, cells, height, copies, gap))
    total = sum(h for _, _, h, _, _ in lanes)
    spare = scene.h - total
    if spare < 8 * len(lanes):
        raise ValueError("too many lanes for the height")
    step = spare / len(lanes)
    y = step / 2
    placed = []
    for index, (entry, cells, height, copies, _gap) in enumerate(lanes):
        art = cells if index % 2 == 0 else np.ascontiguousarray(np.fliplr(cells))   # every other lane flies left
        spacing = scene.w / copies
        shift = (index * 97) % int(spacing)
        for k in range(copies):
            scene.put(art, int(round(shift + k * spacing)), int(round(y)))
        placed.append(f"{entry.name} ({library.speed_text(entry)[0]})")
        y += height + step
    meta = {"title": {"en": "Spaceship parade", "pt": "Desfile de naves"},
            "width": scene.w, "height": scene.h, "view": [0, 0, scene.w, scene.h], "gps": 20,
            "palette": "ice", "x": 0, "y": 0}
    notes = [f"{len(placed)} lanes of spaceships, alternating directions:", ", ".join(placed)]
    return scene, meta, notes


CLOCK_PAD = 256


def clock():
    pattern = rle.parse((ROOT / "sources" / "clockMini.rle").read_text(encoding="utf-8"))
    cells = pattern.cells
    h, w = cells.shape
    width, height = w + 2 * CLOCK_PAD, h + 2 * CLOCK_PAD
    view_h = 4320
    # "view" is the display and the machinery above it, which a 16:9 screen
    # shows whole at 1/2 or 1/4; "focus" is just the digits, for the screens
    # that cannot fit the whole view in a world of at most 8192 cells.
    meta = {"title": {"en": "Digital clock", "pt": "Relógio digital"},
            "width": width, "height": height, "x": CLOCK_PAD, "y": CLOCK_PAD,
            "view": [0, height - view_h, width, view_h], "focus": [2048, 5312, 5440, 2432],
            "gps": 48, "palette": "matrix"}
    notes = ["The digital clock from the Code Golf challenge 'Build a digital clock in Conway's Game of Life'",
             "(codegolf.stackexchange.com/questions/88783): design by 'dim', 2017;",
             "this reduction, clockMini, by Vladan Majerech (github.com/VladanMajerech/ConwayLifeDigitalClocks).",
             "It shows hours and minutes (the AM label of this version stays put); a minute is 2880 generations,",
             "so at 48 generations per second it keeps real time while it is on screen."]
    return cells, meta, notes


def write(path: Path, cells: np.ndarray, meta: dict, name: str, notes: list[str]) -> None:
    lines = rle.to_rle(cells, "B3/S23", name=name).split("\n")
    header = next(i for i, line in enumerate(lines) if line.startswith("x = "))
    extra = [f"#C {n}" for n in notes] + ["#C golwall-world " + json.dumps(meta, ensure_ascii=False,
                                                                          separators=(",", ":"))]
    lines[header:header] = extra
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1024:.0f} KB, {int(cells.sum()):,} cells)")


def check(scene: Scene, generations: int, every: int) -> bool:
    """The whole must stay exactly the sum of its parts."""
    whole = World(scene.w, scene.h)
    whole.load_array(scene.cells())
    alone = []
    for part in scene.parts:
        world = World(scene.w, scene.h)
        world.load_array(scene.cells([part]))
        alone.append(world)
    for gen in range(1, generations + 1):
        whole.step()
        for world in alone:
            world.step()
        if gen % every == 0 or gen == generations:
            union = np.zeros_like(whole.bits)
            for world in alone:
                np.bitwise_or(union, world.bits, out=union)
            if not np.array_equal(union, whole.bits):
                print(f"   objects interact by generation {gen}")
                return False
    print(f"   {len(scene.parts)} objects never touch in {generations} generations")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    clock_cells, clock_meta, clock_notes = clock()
    garden_scene, garden_meta, garden_notes = garden()
    parade_scene, parade_meta, parade_notes = parade()
    if args.check:
        ok = check(garden_scene, 600, 20)
        ok &= check(parade_scene, 3000, 50)          # both run, even if the first fails
        if not ok:
            print("a proof failed: nothing was written")
            return 1
    write(OUT / "1-digital-clock.rle", clock_cells, clock_meta, "Digital clock (clockMini)", clock_notes)
    write(OUT / "2-oscillator-garden.rle", garden_scene.cells(), garden_meta, "Oscillator garden", garden_notes)
    write(OUT / "3-spaceship-parade.rle", parade_scene.cells(), parade_meta, "Spaceship parade", parade_notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
