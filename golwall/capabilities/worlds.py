"""Saved worlds: the examples that ship with golwall, and the ones the user saves.

A world is an RLE file -- so Golly and every other Life program can open it --
with one extra comment line that golwall reads back: the world's size (the
cells alone do not say how much empty space was around them), where the
camera was, the speed, and for the examples a title in each language::

    #C golwall-world {"width": 1280, "height": 720, "gps": 8, "camera": {...}}

The examples live next to the code (inside the executable once built) and are
read-only; the user's worlds live in ``<data dir>/worlds``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..core import rle
from ..core.library import safe_filename
from . import persistence

STARTER_DIR = Path(__file__).resolve().parent.parent / "data" / "worlds"
META_TAG = "golwall-world"
MAX_BYTES = 64 * 1024 * 1024
MAX_META = 64 * 1024             # a meta line is a few hundred bytes; anything huge is not ours


@dataclass
class WorldFile:
    path: Path
    name: str                       # what the list shows
    starter: bool
    meta: dict = field(default_factory=dict)


def user_dir(create: bool = False) -> Path:
    folder = persistence.data_dir() / "worlds"
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def _read_meta(path: Path) -> dict:
    """The golwall comment of a world file, read without parsing the cells."""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
            for _ in range(64):
                line = handle.readline()
                if not line:
                    break
                meta = parse_meta_line(line)
                if meta is not None:
                    return meta
                if line.strip() and not line.lstrip().startswith("#"):
                    break
    except OSError:
        pass
    return {}


def parse_meta_line(line: str) -> dict | None:
    text = line.strip()
    if not text.startswith("#C") and not text.startswith("#c"):
        return None
    body = text[2:].strip()
    if not body.startswith(META_TAG) or len(body) > MAX_META:
        return None
    try:
        meta = json.loads(body[len(META_TAG):].strip())
    except (ValueError, RecursionError):         # a file anyone can edit: never let it crash the list
        return None
    return meta if isinstance(meta, dict) else None


def title_of(meta: dict, fallback: str, language: str) -> str:
    title = meta.get("title")
    if isinstance(title, dict):
        return str(title.get(language) or title.get("en") or fallback)
    if isinstance(title, str) and title:
        return title
    return fallback


def list_worlds(language: str = "en") -> list[WorldFile]:
    """Examples first (in their file order), then the user's worlds by name."""
    out: list[WorldFile] = []
    if STARTER_DIR.is_dir():
        for path in sorted(STARTER_DIR.glob("*.rle")):
            meta = _read_meta(path)
            out.append(WorldFile(path, title_of(meta, path.stem, language), True, meta))
    folder = user_dir()
    if folder.is_dir():
        mine = sorted((p for p in folder.iterdir() if p.suffix.lower() in (".rle", ".cells", ".lif", ".life")
                       and p.is_file()), key=lambda p: p.stem.lower())
        for path in mine:
            out.append(WorldFile(path, path.stem, False, _read_meta(path)))
    return out


def read(path: Path) -> tuple[rle.Pattern, dict]:
    """The cells and golwall's notes about them; raises rle.PatternError or OSError."""
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise rle.PatternError("the file is too large")
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    meta = {}
    for line in text.splitlines()[:64]:
        found = parse_meta_line(line)
        if found is not None:
            meta = found
            break
    return rle.parse(text), meta


def path_for(name: str) -> Path:
    return user_dir(create=True) / (safe_filename(name) + ".rle")


def write(path: Path, cells: np.ndarray, rule: str, name: str, meta: dict) -> Path:
    """Save the whole world, empty space included, atomically."""
    lines = rle.to_rle(cells, rule, name=name).split("\n")
    header = next(i for i, line in enumerate(lines) if line.startswith("x = "))
    lines.insert(header, f"#C {META_TAG} {json.dumps(meta, separators=(',', ':'), ensure_ascii=False)}")
    persistence.atomic_write(Path(path), "\n".join(lines).encode("utf-8"))
    return Path(path)


def delete(path: Path) -> bool:
    path = Path(path)
    try:
        if path.resolve().parent != user_dir().resolve():
            return False                      # never an example, never anything else
        os.remove(path)
        return True
    except OSError:
        return False


def open_folder() -> None:
    os.startfile(str(user_dir(create=True)))          # noqa: S606 - the user's own folder
