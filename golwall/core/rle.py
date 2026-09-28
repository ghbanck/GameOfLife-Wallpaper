"""Reading and writing the pattern formats the Life community actually uses.

``RLE`` is the lingua franca -- LifeWiki, Golly and every pattern collection
publish it -- so being able to paste one in is what turns the wallpaper into a
place to try any of the thousands of known patterns.  Plaintext (``.cells``)
and Life 1.06 coordinate lists are read too, because they turn up just as often
in old collections.  Nothing here touches the world; it only converts text to
arrays of 0/1 and back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

# The largest world the settings allow is 8192x8192, so anything that could be
# saved can be loaded back; beyond that a pattern is a mistake (or hostile).
MAX_CELLS = 8192 * 8192
MAX_SIDE = 100_000                 # checked while parsing, before anything is allocated

_HEADER = re.compile(r"^\s*x\s*=\s*(\d+)\s*,\s*y\s*=\s*(\d+)(?:\s*,\s*rule\s*=\s*([^\s,]+))?",
                     re.IGNORECASE)


class PatternError(ValueError):
    pass


@dataclass
class Pattern:
    cells: np.ndarray
    name: str = ""
    rule: str = ""
    comments: list[str] = field(default_factory=list)

    @property
    def shape(self) -> tuple[int, int]:
        return self.cells.shape


def _check_size(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise PatternError("the pattern is empty")
    if width * height > MAX_CELLS:
        raise PatternError(f"a {width}x{height} pattern is too large to paste")


def parse(text: str) -> Pattern:
    """Detect the format of ``text`` and return the pattern in it."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("﻿ \n\t")
    if not text:
        raise PatternError("there is no pattern here")
    first = text.lstrip().split("\n", 1)[0].strip()
    lines = [ln for ln in text.split("\n") if ln.strip()]
    body = [ln for ln in lines if not ln.lstrip().startswith(("#", "!"))]
    try:
        if first.lower().startswith("#life 1.06"):
            return parse_life106(text)
        if first.lower().startswith("#life 1.05"):
            return parse_life105(text)
        if any(_HEADER.match(ln) for ln in lines) or (body and re.fullmatch(r"[\dbo$!\s]+", "".join(body))
                                                       and "!" in "".join(body)):
            return parse_rle(text)
        return parse_plaintext(text)
    except PatternError:
        raise
    except (ValueError, IndexError, MemoryError) as exc:     # never let odd text escape as a crash
        raise PatternError(f"this is not a readable pattern ({exc})") from None


def parse_rle(text: str) -> Pattern:
    name, rule, comments = "", "", []
    width = height = None
    body: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            tag, _, rest = line[1:].partition(" ")
            if tag in ("N",):
                name = rest.strip()
            elif tag in ("C", "c", "O"):
                comments.append(rest.strip())
            elif tag == "r":
                rule = rest.strip()
            continue
        header = _HEADER.match(line)
        if header and width is None:
            width, height = int(header.group(1)), int(header.group(2))
            rule = header.group(3) or rule
            continue
        body.append(line)
        if "!" in line:
            break

    # Multi-state files name states with capitals: A is state 1, B state 2...
    # In LifeHistory (the format LifeWiki uses for annotated patterns) the odd
    # states are live cells and the even ones only mark where cells have
    # been; elsewhere state 1 is the live one.
    history = rule.replace(" ", "").lower().startswith("lifehistory")
    data = "".join(body)
    multi = history or re.search(r"[A-NP-X]", data) is not None
    rows: list[list[tuple[int, int]]] = [[]]   # per row: (start column, run length) of live runs
    x = 0
    count = ""
    widest = 0
    prefix = False                             # p..y start a two-letter state (25 and up)
    too_large = PatternError("the RLE data describes an absurdly large pattern")
    for ch in data:
        if ch.isdigit():
            count += ch
            if len(count) > 9:
                raise too_large
            continue
        n = int(count) if count else 1
        if ch == "!":
            break
        if ch in " \t":
            continue
        if multi and not prefix and "p" <= ch <= "y":
            prefix = True                        # the count belongs to the letter that follows
            continue
        count = ""
        # Bounds are checked before anything grows: "2000000000$" must not
        # build two billion empty rows first and complain afterwards.
        if ch == "$":
            if len(rows) + n > MAX_SIDE:
                raise too_large
            widest = max(widest, x)
            rows.extend([] for _ in range(n))
            x = 0
            continue
        if x + n > MAX_SIDE:
            raise too_large
        if prefix:
            prefix = False
            if not ch.isupper():
                raise PatternError(f"unexpected {ch!r} in the RLE data")
            x += n                                 # states 25+ are never live here
        elif ch in "b.":
            x += n
        elif ch.isupper():
            state = ord(ch) - 64
            if ch == "O" or ((state % 2 == 1) if history else state == 1):   # O: a stray capital o
                rows[-1].append((x, n))
            x += n
        elif ch.isalpha() or ch == "*":
            rows[-1].append((x, n))
            x += n
        else:
            raise PatternError(f"unexpected {ch!r} in the RLE data")
    widest = max(widest, x)
    while rows and not rows[-1]:
        rows.pop()
    used_h = len(rows)
    width = max(width or 0, widest)
    height = max(height or 0, used_h)
    _check_size(width, height)
    cells = np.zeros((height, width), np.uint8)
    for y, runs in enumerate(rows):
        for start, n in runs:
            cells[y, start:start + n] = 1
    return Pattern(cells, name, rule, comments)


def parse_plaintext(text: str) -> Pattern:
    name, comments, rows = "", [], []
    for raw in text.split("\n"):
        line = raw.rstrip()
        if line.startswith("!"):
            content = line[1:].strip()
            if content.lower().startswith("name:"):
                name = content[5:].strip()
            elif content:
                comments.append(content)
            continue
        if line.startswith("#"):
            comments.append(line[1:].strip())
            continue
        if line and set(line) - set(".oO*x X_-"):
            raise PatternError("this does not look like a Life pattern")
        rows.append(line)
    while rows and not rows[-1].strip():
        rows.pop()
    while rows and not rows[0].strip():
        rows.pop(0)
    width = max((len(r) for r in rows), default=0)
    _check_size(width, len(rows))
    cells = np.zeros((len(rows), width), np.uint8)
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch in "oO*xX":
                cells[y, x] = 1
    return Pattern(cells, name, "", comments)


def parse_life105(text: str) -> Pattern:
    """Life 1.05: rows of . and * in blocks, each block placed by a ``#P x y`` line."""
    points, comments, name = [], [], ""
    x0 = y = 0
    for raw in text.split("\n")[1:]:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            tag, _, rest = line[1:].partition(" ")
            if tag.upper() == "P":
                parts = rest.split()
                try:
                    x0, y = int(parts[0]), int(parts[1])
                except (IndexError, ValueError):
                    raise PatternError(f"bad Life 1.05 line {line[:40]!r}") from None
            elif tag in ("D", "C"):
                comments.append(rest.strip())
            elif tag == "N" and rest.strip():
                name = rest.strip()
            continue
        if set(line) - set(".*oO"):
            raise PatternError("this does not look like a Life 1.05 pattern")
        points.extend((x0 + i, y) for i, ch in enumerate(line) if ch in "*oO")
        y += 1
    if not points:
        raise PatternError("the pattern is empty")
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    width, height = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
    _check_size(width, height)
    cells = np.zeros((height, width), np.uint8)
    for px, py in points:
        cells[py - min(ys), px - min(xs)] = 1
    return Pattern(cells, name, "", comments)


def parse_life106(text: str) -> Pattern:
    points, comments = [], []
    for raw in text.split("\n")[1:]:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            comments.append(line[1:].strip())
            continue
        parts = line.split()
        try:
            if len(parts) != 2:
                raise ValueError
            point = (int(parts[0]), int(parts[1]))
        except ValueError:
            raise PatternError(f"bad Life 1.06 line {line[:40]!r}") from None
        points.append(point)
    if not points:
        raise PatternError("the pattern is empty")
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    width, height = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
    _check_size(width, height)
    cells = np.zeros((height, width), np.uint8)
    for px, py in points:
        cells[py - min(ys), px - min(xs)] = 1
    return Pattern(cells, "", "", comments)


def crop(cells: np.ndarray) -> np.ndarray:
    """Trim empty rows and columns from the edges."""
    ys, xs = np.nonzero(cells)
    if not ys.size:
        return cells[:0, :0]
    return cells[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def to_rle(cells: np.ndarray, rule: str = "B3/S23", name: str = "",
           comment: str = "") -> str:
    """Encode an array of 0/1 as RLE, wrapped at 70 columns like Golly does."""
    cells = np.asarray(cells) != 0
    height, width = cells.shape if cells.ndim == 2 else (0, 0)
    out: list[str] = []
    name = " ".join(str(name).split())
    if name:
        out.append(f"#N {name}")
    if comment:
        out.extend(f"#C {' '.join(line.split())}" for line in comment.splitlines() if line.strip())
    out.append(f"x = {width}, y = {height}, rule = {rule}")

    tokens: list[str] = []
    pending_rows = 0
    for y in range(height):
        row = cells[y]
        live = np.flatnonzero(row)
        if not live.size:
            pending_rows += 1
            continue
        if tokens or pending_rows:
            ends = pending_rows + 1 if tokens else pending_rows
            if ends:
                tokens.append(f"{ends if ends > 1 else ''}$")
        pending_rows = 0
        # Runs of equal cells, stopping at the last live one.
        edges = np.flatnonzero(np.diff(row[:live[-1] + 1].astype(np.int8))) + 1
        starts = np.concatenate(([0], edges))
        stops = np.concatenate((edges, [live[-1] + 1]))
        for a, b in zip(starts, stops):
            n = int(b - a)
            tokens.append(f"{n if n > 1 else ''}{'o' if row[a] else 'b'}")
    tokens.append("!")

    line = ""
    lines = []
    for token in tokens:
        if len(line) + len(token) > 70:
            lines.append(line)
            line = ""
        line += token
    lines.append(line)
    return "\n".join(out + lines) + "\n"
