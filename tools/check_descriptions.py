"""Check the tooltip notes in sources/descriptions against the patterns they describe.

    venv\\Scripts\\python tools\\check_descriptions.py [batch file ...]

For each batch in sources/desc_batches (or the ones named), every pattern must
have a note in English and Portuguese (or both left empty on purpose), short
enough for a tooltip, and written in its own words: no run of six or more
words copied from the source text it was based on.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BATCHES = ROOT / "sources" / "desc_batches"
NOTES = ROOT / "sources" / "descriptions"
MAX_EN, MAX_PT = 220, 240
RUN = 6


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def copied_run(note: str, source: str) -> str:
    """The first run of RUN words that the note shares with the source, if any."""
    src = words(source)
    grams = {tuple(src[i:i + RUN]) for i in range(len(src) - RUN + 1)}
    mine = words(note)
    for i in range(len(mine) - RUN + 1):
        if tuple(mine[i:i + RUN]) in grams:
            return " ".join(mine[i:i + RUN])
    return ""


def load_notes() -> dict:
    notes = {}
    for path in sorted(NOTES.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            print(f"BROKEN JSON {path.name}: {exc}")
            continue
        for key, value in data.items():
            notes[key] = value
    return notes


def check(batch_path: Path, notes: dict) -> list[str]:
    problems = []
    for item in json.loads(batch_path.read_text(encoding="utf-8")):
        canon, name = item["canon"], item["name"]
        note = notes.get(canon)
        if not isinstance(note, dict):
            problems.append(f"{name} [{canon}]: no note")
            continue
        en, pt = str(note.get("en", "")).strip(), str(note.get("pt", "")).strip()
        if bool(en) != bool(pt):
            problems.append(f"{name}: one language is empty")
        if len(en) > MAX_EN:
            problems.append(f"{name}: English note is {len(en)} characters (max {MAX_EN})")
        if len(pt) > MAX_PT:
            problems.append(f"{name}: Portuguese note is {len(pt)} characters (max {MAX_PT})")
        source = " ".join(str(item.get(k, "")) for k in ("lexicon_text", "lifewiki_comments", "golwall_note"))
        run = copied_run(en, source)
        if run:
            problems.append(f"{name}: English note copies the source: '{run}'")
        if re.search(r"\b(below|above|shown here|this diagram)\b", en, re.I):
            problems.append(f"{name}: refers to a diagram the tooltip does not have")
    return problems


def main() -> int:
    notes = load_notes()
    paths = [Path(p) for p in sys.argv[1:]] or sorted(BATCHES.glob("batch_*.json"))
    total = 0
    for path in paths:
        problems = check(path, notes)
        total += len(problems)
        print(f"{path.name}: {'ok' if not problems else f'{len(problems)} problem(s)'}")
        for line in problems[:60]:
            print("   ", line)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
