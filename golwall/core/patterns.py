"""The pattern library: the classic Life objects, as ASCII art.

Each entry carries the period it is supposed to have, which the test suite
uses to check the art really is the object it claims to be -- a single mistyped
dot turns a still life into a mess, and that is easy to miss by eye.

    period 1  a still life (never changes)
    period n  an oscillator that returns to its start after n generations
    period 0  chaotic or unbounded: methuselahs, guns, puffers
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

STILL, OSC, SHIP, METHUSELAH, GUN = (
    "Still lifes", "Oscillators", "Spaceships", "Methuselahs", "Guns & growth")


@dataclass(frozen=True)
class PatternInfo:
    key: str
    title: str
    category: str
    art: str
    period: int = 0
    moves: bool = False       # a spaceship: returns to its shape, displaced
    note: str = ""


LIBRARY: tuple[PatternInfo, ...] = (
    PatternInfo("block", "Block", STILL, """
OO
OO
""", 1),
    PatternInfo("beehive", "Beehive", STILL, """
.OO.
O..O
.OO.
""", 1),
    PatternInfo("loaf", "Loaf", STILL, """
.OO.
O..O
.O.O
..O.
""", 1),
    PatternInfo("boat", "Boat", STILL, """
OO.
O.O
.O.
""", 1),
    PatternInfo("tub", "Tub", STILL, """
.O.
O.O
.O.
""", 1),
    PatternInfo("ship", "Ship", STILL, """
OO.
O.O
.OO
""", 1),
    PatternInfo("pond", "Pond", STILL, """
.OO.
O..O
O..O
.OO.
""", 1),
    PatternInfo("blinker", "Blinker", OSC, """
OOO
""", 2),
    PatternInfo("toad", "Toad", OSC, """
.OOO
OOO.
""", 2),
    PatternInfo("beacon", "Beacon", OSC, """
OO..
OO..
..OO
..OO
""", 2),
    PatternInfo("clock", "Clock", OSC, """
..O.
O.O.
.O.O
.O..
""", 2),
    PatternInfo("pulsar", "Pulsar", OSC, """
..OOO...OOO..
.............
O....O.O....O
O....O.O....O
O....O.O....O
..OOO...OOO..
.............
..OOO...OOO..
O....O.O....O
O....O.O....O
O....O.O....O
.............
..OOO...OOO..
""", 3),
    PatternInfo("pentadecathlon", "Pentadecathlon", OSC, """
..O....O..
OO.OOOO.OO
..O....O..
""", 15),
    PatternInfo("figure_eight", "Figure eight", OSC, """
OOO...
OOO...
OOO...
...OOO
...OOO
...OOO
""", 8),
    PatternInfo("galaxy", "Kok's galaxy", OSC, """
OOOOOO.OO
OOOOOO.OO
.......OO
OO.....OO
OO.....OO
OO.....OO
OO.......
OO.OOOOOO
OO.OOOOOO
""", 8),
    PatternInfo("glider", "Glider", SHIP, """
.O.
..O
OOO
""", 4, moves=True, note="travels diagonally forever"),
    PatternInfo("lwss", "Lightweight spaceship", SHIP, """
O..O.
....O
O...O
.OOOO
""", 4, moves=True),
    PatternInfo("mwss", "Middleweight spaceship", SHIP, """
..O...
O...O.
.....O
O....O
.OOOOO
""", 4, moves=True),
    PatternInfo("hwss", "Heavyweight spaceship", SHIP, """
..OO...
O....O.
......O
O.....O
.OOOOOO
""", 4, moves=True),
    PatternInfo("r_pentomino", "R-pentomino", METHUSELAH, """
.OO
OO.
.O.
""", note="5 cells, chaotic for 1103 generations"),
    PatternInfo("acorn", "Acorn", METHUSELAH, """
.O.....
...O...
OO..OOO
""", note="7 cells, runs for over 5000 generations"),
    PatternInfo("diehard", "Diehard", METHUSELAH, """
......O.
OO......
.O...OOO
""", note="vanishes completely after 130 generations"),
    PatternInfo("b_heptomino", "B-heptomino", METHUSELAH, """
O.OO
OOO.
.O..
""", note="7 cells, settles after 148 generations"),
    PatternInfo("pi_heptomino", "Pi-heptomino", METHUSELAH, """
OOO
O.O
O.O
"""),
    PatternInfo("thunderbird", "Thunderbird", METHUSELAH, """
OOO
...
.O.
.O.
.O.
"""),
    PatternInfo("gosper_gun", "Gosper glider gun", GUN, """
........................O...........
......................O.O...........
............OO......OO............OO
...........O...O....OO............OO
OO........O.....O...OO..............
OO........O...O.OO....O.O...........
..........O.....O.......O...........
...........O...O....................
............OO......................
""", note="emits a glider every 30 generations, forever"),
    PatternInfo("infinite_growth", "Infinite growth", GUN, """
OOO.O
O....
...OO
.OO.O
O.O.O
""", note="5x5 seed whose population grows without bound"),
)

BY_KEY = {p.key: p for p in LIBRARY}
CATEGORIES = (STILL, OSC, SHIP, METHUSELAH, GUN)

_CACHE: dict[str, np.ndarray] = {}


def parse(art: str) -> np.ndarray:
    rows = [line for line in art.strip("\n").splitlines() if line.strip()]
    widths = {len(r) for r in rows}
    if len(widths) != 1:
        raise ValueError(f"ragged pattern: row widths {sorted(widths)}")
    return np.array([[1 if c == "O" else 0 for c in row] for row in rows], np.uint8)


def get(key: str) -> np.ndarray:
    """Return a pattern as an (h, w) uint8 array of 0/1."""
    if key not in _CACHE:
        _CACHE[key] = parse(BY_KEY[key].art)
    return _CACHE[key]


def info(key: str) -> PatternInfo:
    return BY_KEY[key]


def keys() -> tuple[str, ...]:
    return tuple(BY_KEY)


def by_category() -> dict[str, list[PatternInfo]]:
    out: dict[str, list[PatternInfo]] = {c: [] for c in CATEGORIES}
    for p in LIBRARY:
        out[p.category].append(p)
    return out
