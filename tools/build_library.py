"""Build golwall/data/library.bin: every pattern the control panel's library offers.

Sources, kept in ``sources/`` so the build runs offline and gives the same result:

* golwall's own classics (``golwall/core/patterns.py``);
* the Life Lexicon by Stephen A. Silver, CC BY-SA 3.0 (``sources/lexicon.json``,
  fetched from playgameoflife.com/lexicon);
* the LifeWiki pattern collection (``sources/lifewiki-all.zip``, from
  conwaylife.com/patterns/all.zip).

Every pattern is *run* on an unbounded plane to find out what it is -- a still
life, an oscillator and its period, a spaceship and its speed, something that
dies, settles or keeps growing -- because names and comments are too uneven to
sort 5000 files by.  They only decide what a simulation cannot tell apart: a
gun from a puffer, a reflector from a methuselah.

Short notes for the tooltips come from ``sources/descriptions/*.json`` (written
for golwall, in English and Portuguese, from the sources' facts).  Patterns
without one get a sentence built from what the simulation found.

    venv\\Scripts\\python tools\\build_library.py            # build library.bin
    venv\\Scripts\\python tools\\build_library.py --facts    # also write sources/facts.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import zipfile
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from golwall.core import patterns, rle  # noqa: E402

FORMAT_VERSION = 1
MAX_SIDE = 8192                  # the largest world golwall allows
MAX_RLE = 4 * 1024 * 1024        # bytes of RLE per pattern
SIM_MAX_SIDE = 400               # larger patterns are sorted by name only
SIM_MAX_POP = 20_000
SIM_GENERATIONS = 2000
SIM_SECONDS = 4.0
PREVIEW = 64                     # patterns larger than this carry a ready-made brightness map

CATEGORIES = ("classic", "still", "osc", "ship", "gun", "puffer", "methuselah", "wick",
              "circuit", "synth", "big", "other")

GUN = re.compile(r"\bguns?\b|gun$|glider gun|\bgun\b")
PUFFER = re.compile(r"puffer|\brake|breeder|space ?filler|sawtooth|switch engine|\bark\b|"
                    r"block-laying|glider-producing|pufftrain|puff train|growth")
WICK = re.compile(r"\bwick|fuse\b|\bfuses?\b|\bagar|burning|wickstretcher")
CIRCUIT = re.compile(r"eater|reflector|conduit|herschel|converter|splitter|\bto-g\b|g-to|-to-|"
                     r"signal|track|duplicator|inverter|\bgate\b|memory|catalyst|transceiver|"
                     r"snark|bumper|boojum|glider loop|stopper|elbow|kickback|climber|"
                     r"turner|shuttle|hassler|pipsquirter|sparker|injector|lwss-to|mwss-to|hwss-to")
# A periodic object is only engineering when its name says it is one of these:
# shuttles, hasslers and sparkers are oscillators first.
DEVICE = re.compile(r"reflector|conduit|converter|splitter|snark|herschel|duplicator|inverter|\bgate\b|"
                    r"memory|transceiver|glider loop|boojum")
METHUSELAH = re.compile(r"methuselah")
URL = re.compile(r"https?://\S+|www\.\S+|conwaylife\.com\S*")
YEAR = re.compile(r"\b(19[6-9]\d|20[0-3]\d)\b")


# ================================================================ simulation ===
def _crop(c: np.ndarray):
    rows = np.flatnonzero(c.any(axis=1))
    if not rows.size:
        return c[:0, :0], 0, 0
    cols = np.flatnonzero(c.any(axis=0))
    return c[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1], int(cols[0]), int(rows[0])


def _step(c: np.ndarray) -> np.ndarray:
    """One generation on an unbounded plane; the result is one cell larger all round."""
    a = np.pad(c, 2)
    n = (a[:-2, :-2] + a[:-2, 1:-1] + a[:-2, 2:] + a[1:-1, :-2] + a[1:-1, 2:]
         + a[2:, :-2] + a[2:, 1:-1] + a[2:, 2:])
    return ((n == 3) | ((n == 2) & (a[1:-1, 1:-1] == 1))).astype(np.uint8)


def _digest(c: np.ndarray) -> bytes:
    return hashlib.blake2b(np.packbits(c).tobytes() + repr(c.shape).encode(), digest_size=16).digest()


def _symmetric_digest(c: np.ndarray) -> bytes:
    """The same for a pattern however it is rotated or mirrored."""
    options = []
    for flip in (False, True):
        base = np.fliplr(c) if flip else c
        for k in range(4):
            options.append(_digest(np.ascontiguousarray(np.rot90(base, k))))
    return min(options)


def simulate(cells: np.ndarray) -> dict:
    """Run a pattern until it repeats, dies, outgrows the budget or time runs out."""
    c, ox, oy = _crop(cells)
    if not c.size:
        return {"kind": "empty"}
    start_pop, start_side = int(c.sum()), max(c.shape)
    seen = {_digest(c): (0, ox, oy)}
    deadline = time.perf_counter() + SIM_SECONDS
    g = 0
    for g in range(1, SIM_GENERATIONS + 1):
        c, dx, dy = _crop(_step(c))
        ox += dx - 1
        oy += dy - 1
        if not c.size:
            return {"kind": "dies", "lifespan": g}
        key = _digest(c)
        if key in seen:
            g0, x0, y0 = seen[key]
            result = {"kind": "periodic", "transient": g0, "period": g - g0, "dx": ox - x0, "dy": oy - y0,
                      "final_pop": int(c.sum())}
            if g0 == 0:
                # One digest for every phase and orientation, so the same
                # oscillator drawn in another phase is recognised as itself.
                phases, p = [], c
                for _ in range(g - g0):
                    phases.append(_symmetric_digest(p))
                    p, _, _ = _crop(_step(p))
                result["canon"] = min(phases).hex()
            return result
        seen[key] = (g, ox, oy)
        if max(c.shape) > 2048 or c.sum() > 200_000:
            return {"kind": "grows", "at": g}
        if time.perf_counter() > deadline:
            break
    pop = int(c.sum())
    return {"kind": "unresolved", "ran": g, "end_pop": pop, "end_side": max(c.shape),
            "growing": pop > start_pop * 1.5 + 20 or max(c.shape) > start_side * 2 + 40}


def _simulate_job(job):
    key, shape, packed = job
    cells = np.unpackbits(np.frombuffer(packed, np.uint8))[:shape[0] * shape[1]].reshape(shape)
    try:
        return key, simulate(cells), _symmetric_digest(_crop(cells)[0]).hex()
    except MemoryError:
        return key, {"kind": "skipped"}, _symmetric_digest(_crop(cells)[0]).hex()


# ================================================================== sources ===
def _clean_comment(text: str) -> str:
    return URL.sub("", text).strip(" -:;,")


def _wiki_url(comments: list[str]) -> str:
    for line in comments:
        m = re.search(r"(?:https?://)?(?:www\.)?conwaylife\.com/(?:wiki/(?:index\.php\?title=)?)([^\s#]+)", line)
        if m and "patterns/" not in m.group(0) and "ref/" not in m.group(0):
            return "https://conwaylife.com/wiki/" + m.group(1)
    return ""


def load_lifewiki(path: Path) -> list[dict]:
    out = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        rles = {n[:-4] for n in names if n.lower().endswith(".rle")}
        for name in sorted(names):
            lower = name.lower()
            base, ext = os.path.splitext(name)
            if ext.lower() == ".cells" and base in rles:
                continue                                   # the RLE of the same file wins
            if ext.lower() not in (".rle", ".cells"):
                continue
            raw = archive.read(name)
            if len(raw) > MAX_RLE * 2:
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("cp1252", "replace")
            head = re.search(r"x\s*=\s*(\d+)\s*,\s*y\s*=\s*(\d+)", text)
            if head and (int(head.group(1)) > MAX_SIDE or int(head.group(2)) > MAX_SIDE):
                continue
            try:
                pattern = rle.parse(text)
            except rle.PatternError:
                continue
            rule = pattern.rule.replace(" ", "").upper().split(":")[0]
            if rule and rule not in ("B3/S23", "23/3", "LIFE", "LIFEHISTORY", "S23/B3", "B3S23"):
                continue
            cells = rle.crop(pattern.cells)
            if not cells.size:
                continue
            authors = [line[2:].strip() for line in text.splitlines() if line.startswith("#O")]
            comments = [line[2:].strip() for line in text.splitlines() if line.startswith(("#C", "#c"))]
            if ext.lower() == ".cells":
                comments = [c for c in pattern.comments if not c.lower().startswith("name:")]
            display = pattern.name.strip() or base
            if display.lower().endswith((".rle", ".cells")):
                display = os.path.splitext(display)[0]
            out.append({
                "source": "lifewiki", "file": os.path.basename(name), "base": base, "name": display,
                "authors": authors, "notes": [c for c in (_clean_comment(c) for c in comments) if c],
                "url": _wiki_url(comments), "cells": cells,
                "synth": re.search(r"(?<!un)synth", lower) is not None,
            })
    return out


def _plaintext(art: str) -> np.ndarray:
    rows = [line.strip() for line in art.strip("\n").splitlines()]
    while rows and not rows[-1]:
        rows.pop()
    while rows and not rows[0]:
        rows.pop(0)
    width = max((len(r) for r in rows), default=0)
    cells = np.zeros((len(rows), width), np.uint8)
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch in "O*o":
                cells[y, x] = 1
    return cells


def load_lexicon(path: Path) -> list[dict]:
    out = []
    for entry in json.loads(path.read_text(encoding="utf-8")):
        art = entry.get("cells") or ""
        if "O" not in art:
            continue
        cells = rle.crop(_plaintext(art))
        if not cells.size or max(cells.shape) > MAX_SIDE:
            continue
        prose = re.sub(r"\s+", " ", entry.get("prose") or "").strip()
        meta = (entry.get("meta") or "").strip()
        if meta and prose.startswith(meta):
            prose = prose[len(meta):].strip()
        out.append({
            "source": "lexicon", "term": entry["term"],
            "name": (entry.get("name") or entry["term"]).replace("_", " "),
            "meta": meta, "prose": prose, "cells": cells,
            "lexicon_url": "https://playgameoflife.com" + entry.get("href", ""),
        })
    return out


def load_classics() -> list[dict]:
    return [{"source": "golwall", "key": p.key, "name": p.title, "cells": patterns.get(p.key),
             "note": p.note, "category": p.category} for p in patterns.LIBRARY]


# ================================================================ assembling ===
def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "pattern"


def speed_text(dx: int, dy: int, period: int) -> tuple[str, str]:
    """('c/4', 'diagonal') style notation for a spaceship's displacement per period."""
    a, b = sorted((abs(dx), abs(dy)), reverse=True)
    if b == 0:
        direction = "orthogonal"
    elif a == b:
        direction = "diagonal"
    else:
        direction = "oblique"
    g = math.gcd(a, period)
    num, den = a // g, period // g
    speed = f"{'' if num == 1 else num}c/{den}" if den != 1 else f"{num}c"
    if direction == "oblique":
        speed = f"({a},{b})c/{period}"
    return speed, direction


def year_of(*texts: str) -> int | None:
    for text in texts:
        m = YEAR.search(text or "")
        if m:
            return int(m.group(1))
    return None


def found_by(prose: str) -> str:
    m = re.search(r"\b(?:[Ff]ound|[Dd]iscovered|[Cc]onstructed|[Bb]uilt) by "
                  r"([A-Z][\w'-]+(?:\s(?:[A-Z]\.|[A-Z][\w'-]+)){0,3})", prose)
    return m.group(1).strip() if m else ""


def categorise(item: dict) -> str:
    sim = item["sim"]
    kind = sim.get("kind")
    # What the pattern is called decides gun or puffer; what the sources say
    # about it may also mention the guns and puffers it is used with.
    names = " ".join([item["name"], item.get("base", ""), " ".join(item.get("aliases", [])),
                      item.get("meta", "")]).lower()
    text = " ".join([names, " ".join(item.get("notes", [])[:2]), (item.get("prose") or "")[:120]]).lower()
    if item.get("synth"):
        return "synth"
    moving = kind == "periodic" and bool(sim["dx"] or sim["dy"])
    if kind == "periodic" and (sim["transient"] == 0 or moving):
        if sim["period"] == 1:
            return "still"
        if sim["dx"] or sim["dy"]:
            return "ship"
        if DEVICE.search(item["name"].lower()) and int(item["cells"].sum()) > 40:
            return "circuit"                    # a reflector shown with its own signal loop
        return "osc"
    growing = kind in ("grows", "skipped") or (kind == "unresolved" and sim.get("growing"))
    if GUN.search(names) and kind != "dies":
        return "gun"
    if PUFFER.search(names) and kind != "dies":
        return "puffer"
    if WICK.search(text):
        return "wick"
    if CIRCUIT.search(text):
        return "circuit"
    pop = int(item["cells"].sum())
    life = sim.get("lifespan") or sim.get("transient") or (sim.get("ran") if kind == "unresolved" else 0) or 0
    if METHUSELAH.search(text) or (pop <= 40 and life >= 100 and kind in ("periodic", "dies", "unresolved")):
        return "methuselah"
    if max(item["cells"].shape) >= 400 or kind == "skipped":
        return "big"
    if growing:
        return "puffer" if pop < 500 else "big"
    return "other"


def _name_rank(name: str) -> tuple:
    """Lower is a better display name: words beat codes, codes beat file names."""
    code = re.fullmatch(r"[\w.,/-]+", name) is not None and any(ch.isdigit() for ch in name)
    file_like = " " not in name and name[1:] == name[1:].lower() and len(name) > 12
    return (code, file_like, len(name), name)


# Words seen in the collections' spaced-out names, for splitting file names
# such as "pentadecathlonsynthesis" back into "Pentadecathlon synthesis".
VOCABULARY: set[str] = set()


def _split(run: str, words: set[str]) -> list[str] | None:
    """``run`` as a sequence of known words, favouring few long ones; None if impossible."""
    best: list[tuple[int, list[str]] | None] = [None] * (len(run) + 1)
    best[0] = (0, [])
    for end in range(1, len(run) + 1):
        for start in range(max(0, end - 24), end):
            piece = run[start:end]
            if best[start] is None or piece not in words:
                continue
            score = best[start][0] + len(piece) ** 2
            if best[end] is None or score > best[end][0]:
                best[end] = (score, best[start][1] + [piece])
    return best[-1][1] if best[-1] is not None else None


def learn_words(names) -> None:
    """Collect the words of every spaced-out name, minus squashed compounds of other words."""
    seen: set[str] = set()
    for name in names:
        text = name.lower().replace("_", " ").strip()
        if " " not in text:
            continue
        seen.update(w for w in re.findall(r"[a-z]+", text) if len(w) >= 2)
    short = {w for w in seen if len(w) >= 3}
    for word in seen:
        pieces = _split(word, short - {word}) if len(word) >= 8 else None
        if not (pieces and len(pieces) >= 2):       # "queenbeeshuttle" is three words, not one
            VOCABULARY.add(word)


def unsquash(name: str) -> str:
    """'p60glidershuttle' -> 'p60 glider shuttle' when every piece is a known word, else unchanged."""
    if " " in name or len(name) < 9 or name[1:] != name[1:].lower():
        return name
    words: list[str] = []
    for token in re.findall(r"\d+x\d+|[a-z]+|\d+|[^a-z\d]+", name.lower()):
        if token.isdigit() and words and len(words[-1]) <= 2 and words[-1].isalpha():
            words[-1] += token                           # p60, c2, xs4
        elif not token.isalpha() or len(token) <= 2:
            words.append(token)                          # 10x10, 3, p, xs
        else:
            pieces = _split(token, VOCABULARY)
            if pieces is None:
                return name                              # an unknown piece: leave the name alone
            words.extend(pieces)
    return " ".join(w for w in words if w.strip())


def display_name(name: str) -> str:
    """Tidy a source's name: 'queen_bee_shuttle' -> 'Queen bee shuttle'."""
    name = unsquash(name.replace("_", " ").strip())
    first = name.split(" ")[0]
    if name[:1].islower() and not any(ch.isdigit() for ch in first):
        name = name[0].upper() + name[1:]
    return name


def _letters(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def merge(items: list[dict]) -> dict:
    """Several sources describing the same pattern become one entry.

    The name comes from LifeWiki when it has the pattern: a Lexicon diagram
    often only illustrates a term ("glider synthesis", "relay") with a
    pattern that has a name of its own.  A Lexicon name wins only when it is
    the same name better spelled ("Queen bee shuttle pair" for
    "queenbeeshuttlepair").
    """
    lexicon = [i for i in items if i["source"] == "lexicon"]
    wiki = [i for i in items if i["source"] == "lifewiki"]
    names = []
    for i in wiki + lexicon:
        n = display_name(i["name"])
        if n and n not in names:
            names.append(n)
    ranked = sorted(wiki, key=lambda i: _name_rank(i["name"]))
    primary = (ranked or lexicon)[0]
    name = display_name(primary["name"])
    for lx in lexicon:
        better = display_name(lx["name"])
        if _letters(better) == _letters(name) and " " in better and " " not in name:
            name = better
            break
    merged = dict(primary)
    merged["name"] = name
    merged["aliases"] = [n for n in names if n != name]
    by_rank = ranked or wiki
    credited = next((i for i in by_rank if i.get("authors")), None)
    merged["authors"] = credited["authors"] if credited else []
    merged["credit_notes"] = credited["notes"] if credited else []
    merged["notes"] = next((i["notes"] for i in by_rank if i.get("notes")), [])
    merged["url"] = next((i["url"] for i in by_rank if i.get("url")), "")
    merged["files"] = [i["file"] for i in wiki]
    merged["synth"] = any(i.get("synth") for i in wiki)
    if lexicon:
        lx = lexicon[0]
        merged.update(meta=lx["meta"], prose=lx["prose"], term=lx["term"], lexicon_url=lx["lexicon_url"])
    merged["sources"] = sorted({i["source"] for i in items})
    return merged


MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december|"
          "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")


def clean_authors(authors: list[str]) -> str:
    """'Martin Grant, 25 June 2015, Jeremy Tan, 19 March, 2015' -> 'Martin Grant, Jeremy Tan'.

    '#O' lines mix names with dates, e-mail addresses and database stamps in
    every order; they are split at commas and semicolons and whatever is not
    a name is dropped.
    """
    month = rf"\b({MONTHS})\b\.?"
    dropped = [
        rf"^(\d{{1,2}}(st|nd|rd|th)?\s+)?{month}",               # 20 July (2019), July 20
        r"^\d{1,4}([-/.]\d{1,2}([-/.]\d{2,4})?)?$",             # 2019, 12/6/91
        r"^\d{1,2}-[a-z]{3,9}-\d{2,4}",                          # 2-Sep-1999
        r"^(mon|tue|wed|thu|fri|sat|sun)\b",                     # Thu Feb 19 02:01:39
        r"@",                                                   # e-mail addresses
    ]
    names = []
    for raw in authors:
        text = re.sub(r"\([^)]*\)|\[[^]]*\]", "", raw)
        text = re.sub(r"'s\s+life synthesis database", "", text, flags=re.I)
        for part in re.split(r"[,;]", text):
            part = part.strip(" .:-")
            part = re.sub(rf"\s+{month}.*$", "", part, flags=re.I)                 # Mark Walsh Aug 3 2011
            part = re.sub(r"\s+(19|20)\d\d$", "", part).strip(" .:-")
            if not part or any(re.search(p, part, re.I) for p in dropped):
                continue
            if part.lower() in ("unknown", "?", "anonymous") or part in names:
                continue
            names.append(part)
    return ", ".join(names)[:80]


def facts_of(entry: dict) -> dict:
    sim = entry["sim"]
    h, w = entry["cells"].shape
    authors = entry.get("authors") or []
    prose = entry.get("prose", "")
    who = clean_authors(authors)
    # "Found by X in 1970" in the Lexicon: its year counts only for the same X.
    found = re.search(r"\b(?:[Ff]ound|[Dd]iscovered|[Cc]onstructed|[Bb]uilt) by [^.]*", prose)
    found_text = found.group(0) if found else ""
    if who:
        # When the file names who made it, its own lines say when.
        # A year from the '#O' line itself, or from a comment that says it was
        # found then -- not from "as of June 2018" or someone else's variant.
        notes = " ".join(entry.get("credit_notes") or entry.get("notes", []))
        dated = re.search(r"\b(?:found|discovered|constructed|built|completed|created)\b[^.]*?\b(19[6-9]\d|20[0-3]\d)\b",
                          notes, re.I)
        year = year_of(" ".join(authors)) or (int(dated.group(1)) if dated else None)
        surnames = {part.split()[-1].lower() for part in re.split(r",| and ", who) if part.split()}
        if year is None and found_text and any(name in found_text.lower() for name in surnames):
            year = year_of(found_text)
    else:
        who = found_by(prose)
        year = year_of(found_text) if found_text else year_of(" ".join(entry.get("notes", [])))
    fact = {"key": entry["key"], "canon": entry.get("canon", ""), "name": entry["name"],
            "category": entry["category"],
            "width": int(w), "height": int(h), "population": int(entry["cells"].sum()),
            "kind": sim.get("kind", ""), "by": who, "year": year}
    if sim.get("kind") == "periodic" and sim["transient"] and not (sim["dx"] or sim["dy"]):
        fact["kind"] = "settles"
        fact["settles_after"] = sim["transient"]
    elif sim.get("kind") == "periodic":
        fact["period"] = sim["period"]
        if sim["dx"] or sim["dy"]:
            fact["speed"], fact["direction"] = speed_text(sim["dx"], sim["dy"], sim["period"])
    elif sim.get("kind") == "dies":
        fact["dies_after"] = sim["lifespan"]
    return fact


def build(args) -> None:
    started = time.perf_counter()
    lifewiki = load_lifewiki(ROOT / args.lifewiki)
    lexicon = load_lexicon(ROOT / args.lexicon)
    classics = load_classics()
    print(f"read {len(lifewiki)} LifeWiki files, {len(lexicon)} Lexicon entries, {len(classics)} classics "
          f"({time.perf_counter() - started:.1f}s)")

    items = lifewiki + lexicon
    # Running 5000 patterns takes minutes; the results only depend on the cells.
    cache_path = ROOT / "sources" / "sim_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    jobs = []
    for index, item in enumerate(items):
        cells = item["cells"]
        ident = _digest(cells).hex()
        item["ident"] = ident
        if max(cells.shape) > SIM_MAX_SIDE or cells.sum() > SIM_MAX_POP:
            item["sim"] = {"kind": "skipped"}
            item["symmetric"] = ident
        elif ident in cache:
            item["sim"], item["symmetric"] = cache[ident]
        else:
            jobs.append((index, cells.shape, np.packbits(cells).tobytes()))
    if jobs:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            for done, (index, sim, symmetric) in enumerate(pool.map(_simulate_job, jobs, chunksize=8), 1):
                items[index]["sim"] = sim
                items[index]["symmetric"] = symmetric
                cache[items[index]["ident"]] = [sim, symmetric]
                if done % 500 == 0:
                    print(f"  simulated {done}/{len(jobs)} ({time.perf_counter() - started:.0f}s)", flush=True)
        cache_path.write_text(json.dumps(cache, separators=(",", ":")), encoding="utf-8")
    print(f"simulated {len(jobs)} patterns, {len(items) - len(jobs)} known ({time.perf_counter() - started:.1f}s)")

    # -- one entry per distinct pattern ------------------------------------------
    learn_words([i["name"] for i in items] + [i.get("base", "") for i in items])
    groups: dict[str, list[dict]] = {}
    for item in items:
        canon = item["sim"].get("canon") or item["symmetric"]
        groups.setdefault(canon, []).append(item)
    entries = []
    used = set(p.key for p in patterns.LIBRARY)
    for canon, group in groups.items():
        entry = merge(group)
        entry["canon"] = canon
        # Notes written before a change of digest are still found by the old one.
        entry["alt_canons"] = sorted({i["symmetric"] for i in group if i.get("symmetric")})
        stem = slug(entry.get("base") or entry.get("term") or entry["name"])
        key, n = "x:" + stem, 2
        while key in used:
            key = f"x:{stem}-{n}"
            n += 1
        used.add(key)
        entry["key"] = key
        entry["category"] = categorise(entry)
        entries.append(entry)

    # -- the classics borrow what the library knows about the same pattern ----------
    by_canon = {e["canon"]: e for e in entries}
    for classic in classics:
        sim = simulate(classic["cells"])
        canon = sim.get("canon") or _symmetric_digest(_crop(classic["cells"])[0]).hex()
        twin = by_canon.get(canon)
        classic.update(sim=sim, category="classic", aliases=[], authors=[], notes=[], url="",
                       sources=["golwall"], canon=canon)
        if twin is not None:
            for field in ("authors", "credit_notes", "notes", "url", "prose", "meta", "term", "lexicon_url"):
                if twin.get(field):
                    classic[field] = twin[field]
            classic["twin"] = twin["key"]
            classic["sources"] = sorted({"golwall", *twin["sources"]})
        else:
            print(f"  warning: the classic {classic['key']!r} matches no library pattern -- is its art right?")
    entries = classics + entries

    for entry in entries:
        entry["facts"] = facts_of(entry)

    descriptions = {}
    folder = ROOT / "sources" / "descriptions"
    for path in sorted(folder.glob("*.json")) if folder.exists() else ():
        for key, value in json.loads(path.read_text(encoding="utf-8")).items():
            descriptions[key] = value
    for entry in entries:
        note = (descriptions.get(entry.get("canon", ""))
                or next((descriptions[c] for c in entry.get("alt_canons", []) if c in descriptions), None)
                or descriptions.get(entry["key"]))
        entry["note_en"] = (note or {}).get("en", "")
        entry["note_pt"] = (note or {}).get("pt", "")

    if args.facts:
        write_facts(entries, ROOT / "sources" / "facts.json")

    # -- write library.bin ------------------------------------------------------------
    order = {c: i for i, c in enumerate(CATEGORIES)}
    entries.sort(key=lambda e: (order[e["category"]], e["category"] != "classic" and e["name"].lower()))
    blob = bytearray()
    rows = []
    skipped = 0
    for entry in entries:
        body = rle.to_rle(entry["cells"]).split("\n", 1)[1]
        if len(body) > MAX_RLE:
            skipped += 1
            continue
        data = body.encode("ascii")
        preview = b""
        if max(entry["cells"].shape) > PREVIEW:
            preview = preview_of(entry["cells"])
        sim, fact = entry["sim"], entry["facts"]
        rows.append([
            entry["key"], entry["name"], entry["category"], fact["width"], fact["height"],
            fact["population"], fact.get("period", 0), sim.get("dx", 0) if fact.get("period") else 0,
            sim.get("dy", 0) if fact.get("period") else 0,
            fact.get("settles_after") or fact.get("dies_after") or 0, fact["kind"], fact["by"],
            fact["year"] or 0, entry["sources"], entry.get("url") or entry.get("lexicon_url") or "",
            entry["note_en"], entry["note_pt"], entry.get("aliases", []), len(blob), len(data),
            len(blob) + len(data) if preview else 0, len(preview), entry.get("twin", ""),
        ])
        blob += data + preview
    header = json.dumps({
        "version": FORMAT_VERSION,
        "preview": PREVIEW,
        "fields": ["key", "name", "category", "width", "height", "population", "period", "dx", "dy",
                   "lifespan", "kind", "by", "year", "sources", "url", "note_en", "note_pt", "aliases",
                   "offset", "length", "preview_offset", "preview_length", "twin"],
        "patterns": rows,
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = len(header).to_bytes(4, "little") + header + bytes(blob)
    out.write_bytes(b"GOLLIB1\0" + zlib.compress(payload, 9))
    counts = {}
    for row in rows:
        counts[row[2]] = counts.get(row[2], 0) + 1
    with_notes = sum(1 for row in rows if row[15])
    print(f"wrote {out.relative_to(ROOT)}: {len(rows)} patterns ({skipped} too large), "
          f"{out.stat().st_size / 1e6:.1f} MB, {with_notes} with notes; {counts}")
    print(f"done in {time.perf_counter() - started:.1f}s")


def preview_of(cells: np.ndarray) -> bytes:
    """A PREVIEW x PREVIEW brightness map, so big patterns get an icon without decoding megabytes."""
    h, w = cells.shape
    factor = math.ceil(max(h, w) / PREVIEW)
    ph, pw = -(-h // factor) * factor, -(-w // factor) * factor
    padded = np.zeros((ph, pw), np.float32)
    padded[:h, :w] = cells
    density = padded.reshape(ph // factor, factor, pw // factor, factor).mean(axis=(1, 3))
    level = np.sqrt(np.minimum(1.0, density * 3.0))          # sparse machinery stays visible
    out = np.zeros((PREVIEW, PREVIEW), np.uint8)
    oy, ox = (PREVIEW - level.shape[0]) // 2, (PREVIEW - level.shape[1]) // 2
    out[oy:oy + level.shape[0], ox:ox + level.shape[1]] = np.round(level * 255).astype(np.uint8)
    return out.tobytes()


def write_facts(entries: list[dict], path: Path) -> None:
    """What a writer needs to describe each pattern: the facts plus the sources' own notes."""
    out = []
    for entry in entries:
        item = dict(entry["facts"])
        if entry.get("aliases"):
            item["aliases"] = entry["aliases"][:4]
        if entry.get("meta"):
            item["lexicon_meta"] = entry["meta"]
        if entry.get("prose"):
            item["lexicon_text"] = entry["prose"][:900]
        if entry.get("notes"):
            item["lifewiki_comments"] = " ".join(entry["notes"])[:700]
        if entry.get("note") and entry["source"] == "golwall":
            item["golwall_note"] = entry["note"]
        if entry.get("twin"):
            item["twin"] = entry["twin"]
        out.append(item)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)} ({len(out)} entries)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--lexicon", default="sources/lexicon.json")
    parser.add_argument("--lifewiki", default="sources/lifewiki-all.zip")
    parser.add_argument("--out", default="golwall/data/library.bin")
    parser.add_argument("--facts", action="store_true", help="also write sources/facts.json")
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    build(parser.parse_args())


if __name__ == "__main__":
    main()
