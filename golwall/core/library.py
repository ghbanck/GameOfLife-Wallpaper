"""The pattern library the control panel browses: every known pattern, and yours.

``data/library.bin`` is built by ``tools/build_library.py`` from golwall's
classics, the Life Lexicon (Stephen A. Silver, CC BY-SA 3.0) and the LifeWiki
pattern collection: about 4800 patterns, each with what a simulation found out
about it (period, speed, lifespan), who found it, and a short note for the
tooltip.  Cells stay as RLE until something asks for them, so browsing the
list costs a few megabytes rather than the hundreds that 4800 decoded arrays
would.

Files dropped into the user's pattern folder show up as one more category,
"My patterns", read when the panel opens or asks for a refresh.

Both the panel thread (browsing, icons) and the main thread (stamping) read
from here, so everything that is loaded or cached lazily is guarded by a lock.
"""

from __future__ import annotations

import json
import os
import threading
import zlib
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import patterns, rle

CATEGORIES = ("classic", "still", "osc", "ship", "gun", "puffer", "methuselah", "wick",
              "circuit", "synth", "big", "other", "mine")
USER_CATEGORY = "mine"
USER_PREFIX = "user:"
USER_EXTENSIONS = (".rle", ".cells", ".lif", ".life", ".txt")
USER_MAX_FILES = 500
USER_MAX_BYTES = 16 * 1024 * 1024
DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "library.bin"
MAGIC = b"GOLLIB1\0"


@dataclass(frozen=True)
class Entry:
    key: str
    name: str
    category: str
    width: int
    height: int
    population: int
    period: int = 0              # 1 still life, n oscillator/spaceship, 0 not periodic
    dx: int = 0                  # displacement per period (spaceships)
    dy: int = 0
    lifespan: int = 0            # generations until it settles or dies, when known
    kind: str = ""               # what the simulation found: periodic, dies, grows...
    by: str = ""
    year: int = 0
    sources: tuple[str, ...] = ()
    url: str = ""
    note_en: str = ""
    note_pt: str = ""
    aliases: tuple[str, ...] = ()
    offset: int = -1             # where the RLE lives in the blob
    length: int = 0
    preview_offset: int = 0
    preview_length: int = 0
    twin: str = ""               # a classic: the library entry that is the same pattern
    path: str = ""               # a user file
    search: str = field(default="", compare=False)

    @property
    def moves(self) -> bool:
        return bool(self.dx or self.dy)


class Library:
    def __init__(self, data_file: Path | None = DATA_FILE, user_dir: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._blob = b""
        self.preview_size = 0
        self.error = ""
        self.entries: list[Entry] = []
        self.by_key: dict[str, Entry] = {}
        self._cells: OrderedDict[str, np.ndarray] = OrderedDict()
        self._user: list[Entry] = []
        self._scanned: dict[str, tuple[int, int, Entry | None]] = {}      # path -> (size, mtime, entry)
        self.user_dir = user_dir
        builtin = self._read(data_file) if data_file is not None else []
        if not builtin:
            builtin = self._classics_only()
        self._builtin = builtin
        self._index()

    # -- loading ------------------------------------------------------------
    def _read(self, path: Path) -> list[Entry]:
        try:
            raw = path.read_bytes()
            if not raw.startswith(MAGIC):
                raise ValueError("not a pattern library")
            payload = zlib.decompress(raw[len(MAGIC):])
            size = int.from_bytes(payload[:4], "little")
            header = json.loads(payload[4:4 + size].decode("utf-8"))
            self._blob = payload[4 + size:]
            self.preview_size = int(header.get("preview", 0))
            fields = header["fields"]
            out = []
            for row in header["patterns"]:
                item = dict(zip(fields, row))
                item["sources"] = tuple(item.get("sources") or ())
                item["aliases"] = tuple(item.get("aliases") or ())
                out.append(Entry(**{k: v for k, v in item.items() if k in _ENTRY_FIELDS}))
            return out
        except (OSError, ValueError, KeyError, TypeError, zlib.error) as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            return []

    @staticmethod
    def _classics_only() -> list[Entry]:
        out = []
        for info in patterns.LIBRARY:
            art = patterns.get(info.key)
            # The glider flies a cell diagonally per period, the others two across.
            dx, dy = ((1, 1) if info.key == "glider" else (2, 0)) if info.moves else (0, 0)
            out.append(Entry(info.key, info.title, "classic", art.shape[1], art.shape[0], int(art.sum()),
                             period=info.period, dx=dx, dy=dy, note_en=info.note, note_pt=info.note))
        return out

    def _index(self) -> None:
        with self._lock:
            entries = [e if e.search else _with_search(e) for e in self._builtin + self._user]
            self.entries = entries
            self.by_key = {e.key: e for e in entries}

    # -- the user's folder --------------------------------------------------------
    def refresh_user(self) -> int:
        """Re-read the user's pattern folder; returns how many patterns it holds.

        Only new or changed files are parsed -- saving one pattern from the
        editor must not re-read a folder of hundreds -- and no cells are kept:
        cells() decodes a user pattern when it is needed, under the same
        memory budget as the library's own.
        """
        found: list[Entry] = []
        scanned: dict[str, tuple[int, int, Entry | None]] = {}
        folder = self.user_dir
        if folder is not None and folder.is_dir():
            try:
                files = sorted((p for p in folder.iterdir()
                                if p.suffix.lower() in USER_EXTENSIONS and p.is_file()),
                               key=lambda p: p.name.lower())[:USER_MAX_FILES]
            except OSError:
                files = []
            for path in files:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                stamp = (stat.st_size, stat.st_mtime_ns)
                known = self._scanned.get(str(path))
                if known is not None and known[:2] == stamp:
                    entry = known[2]
                else:
                    entry = self._read_user(path, stat.st_size)
                    with self._lock:
                        self._cells.pop(USER_PREFIX + path.name, None)       # it may have changed
                scanned[str(path)] = (*stamp, entry)
                if entry is not None:
                    found.append(entry)
        with self._lock:
            keys = {e.key for e in found}
            for old in self._user:
                if old.key not in keys:
                    self._cells.pop(old.key, None)
            self._user = found
            self._scanned = scanned
        self._index()
        return len(found)

    @staticmethod
    def _read_user(path: Path, size: int) -> Entry | None:
        if size > USER_MAX_BYTES:
            return None
        try:
            pattern = rle.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
        except (OSError, rle.PatternError, RecursionError):
            return None
        cells = rle.crop(pattern.cells)
        if not cells.size:
            return None
        note = " ".join(c for c in pattern.comments
                        if not c.lower().startswith(("http", "www.", "golwall-world")))
        return Entry(USER_PREFIX + path.name, (" ".join(pattern.name.split()) or path.stem)[:120],
                     USER_CATEGORY, cells.shape[1], cells.shape[0], int(cells.sum()),
                     note_en=note[:300], note_pt=note[:300], path=str(path))

    # -- browsing -----------------------------------------------------------------
    def categories(self) -> list[tuple[str, list[Entry]]]:
        groups: dict[str, list[Entry]] = {c: [] for c in CATEGORIES}
        for entry in self.entries:
            groups.setdefault(entry.category, []).append(entry)
        return [(c, groups[c]) for c in CATEGORIES if groups.get(c) or c == USER_CATEGORY]

    def search(self, text: str, limit: int = 0) -> list[Entry]:
        """Entries whose name (or another name) holds every word of ``text``."""
        words = text.lower().split()
        if not words:
            return []
        hits = [e for e in self.entries if all(w in e.search for w in words)]
        first = words[0]
        order = {c: i for i, c in enumerate(CATEGORIES)}

        def rank(e: Entry):
            name = e.name.lower()
            return (name != text.lower().strip(), not name.startswith(first), order.get(e.category, 99),
                    len(name), name)

        hits.sort(key=rank)
        # The classics repeat patterns found in other categories; list each once.
        twins = {e.twin for e in hits if e.twin}
        out = [e for e in hits if e.key not in twins]
        return out[:limit] if limit else out

    def get(self, key: str) -> Entry | None:
        return self.by_key.get(key)

    # -- cells ----------------------------------------------------------------------
    def cells(self, key: str) -> np.ndarray:
        """The pattern as an (h, w) uint8 array of 0/1.  Raises KeyError for an unknown key."""
        with self._lock:
            cached = self._cells.get(key)
            if cached is not None:
                self._cells.move_to_end(key)
                return cached
            entry = self.by_key.get(key)
        if entry is None:
            if key in patterns.BY_KEY:
                return patterns.get(key)
            raise KeyError(key)
        if entry.path:
            pattern = rle.parse(Path(entry.path).read_text(encoding="utf-8-sig", errors="replace"))
            cells = rle.crop(pattern.cells)
        elif entry.offset >= 0 and self._blob:
            body = self._blob[entry.offset:entry.offset + entry.length].decode("ascii")
            cells = rle.parse(f"x = {entry.width}, y = {entry.height}\n{body}").cells
        else:
            cells = patterns.get(key)
        # A copy, never a view: a crop of a file that declares a huge size would
        # otherwise keep the whole declared array alive behind a few cells.
        cells = np.array(cells, dtype=np.uint8, copy=True)
        with self._lock:
            self._cells[key] = cells
            # Keep what the editor and the icons use; a 7000x4000 pattern is 28 MB.
            total = sum(c.size for c in self._cells.values())
            while len(self._cells) > 1 and (len(self._cells) > 256 or total > 96 * 1024 * 1024):
                _, dropped = self._cells.popitem(last=False)
                total -= dropped.size
        return cells

    def preview(self, key: str) -> np.ndarray | None:
        """The ready-made brightness map of a big pattern (preview_size square), or None."""
        entry = self.by_key.get(key)
        if entry is None or not entry.preview_length or not self._blob:
            return None
        raw = self._blob[entry.preview_offset:entry.preview_offset + entry.preview_length]
        size = self.preview_size
        if len(raw) != size * size:
            return None
        return np.frombuffer(raw, np.uint8).reshape(size, size)


_ENTRY_FIELDS = set(Entry.__dataclass_fields__)


def _with_search(entry: Entry) -> Entry:
    from dataclasses import replace
    text = " ".join((entry.name, *entry.aliases, entry.key.split(":", 1)[-1])).lower()
    return replace(entry, search=text)


_LIBRARY: Library | None = None
_LOAD_LOCK = threading.Lock()
USER_DIR: Path | None = None          # set by the app: <data dir>/patterns


def get() -> Library:
    """The shared library, loaded on first use (from any thread)."""
    global _LIBRARY
    if _LIBRARY is None:
        with _LOAD_LOCK:
            if _LIBRARY is None:
                lib = Library(DATA_FILE, USER_DIR)
                lib.refresh_user()
                _LIBRARY = lib
    return _LIBRARY


def cells(key: str) -> np.ndarray:
    """A pattern's cells by key; the classics work even before the library is loaded."""
    if _LIBRARY is None and key in patterns.BY_KEY:
        return patterns.get(key)
    return get().cells(key)


def has(key: str) -> bool:
    if key in patterns.BY_KEY:
        return True
    return get().get(key) is not None


def user_folder() -> Path | None:
    if USER_DIR is None:
        return None
    try:
        USER_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return USER_DIR


def speed_text(entry: Entry) -> tuple[str, str]:
    """('c/4', 'diagonal')-style speed of a spaceship, or ('', '')."""
    if not entry.moves or not entry.period:
        return "", ""
    a, b = sorted((abs(entry.dx), abs(entry.dy)), reverse=True)
    if b and a != b:
        return f"({a},{b})c/{entry.period}", "oblique"
    from math import gcd
    g = gcd(a, entry.period)
    num, den = a // g, entry.period // g
    speed = f"{'' if num == 1 else num}c/{den}" if den != 1 else f"{num}c"
    return speed, ("orthogonal" if b == 0 else "diagonal")


def safe_filename(name: str) -> str:
    """A name the user typed, made safe to be a file name in the pattern or world folder."""
    keep = "".join(ch if (ch.isalnum() or ch in " -_.()") else "_" for ch in name).strip(" .")
    keep = keep[:80] or "pattern"
    if keep.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                                      *(f"LPT{i}" for i in range(1, 10))}:
        keep = "_" + keep
    return keep


def open_folder(path: Path) -> None:
    os.startfile(str(path))                        # noqa: S606 - the user's own folder
