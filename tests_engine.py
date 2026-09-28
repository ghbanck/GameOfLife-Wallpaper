"""Self-checks for the banded, threaded simulation engine (golwall.core.life).

Run with ``venv\\Scripts\\python tests_engine.py``.  Every check compares the
bit-packed engine with the textbook rule computed a byte per cell, for many
rules and sizes, with bands and threads forced onto small worlds so the band
seams, the wrapped first and last bands and an uneven last band are all
exercised.  The benchmark at the end is informational, except that a
1280x720 generation must stay under 2 ms.
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager

import numpy as np

from golwall.core import life
from golwall.core.life import World, normalise_rule, parse_rule

FAILURES: list[str] = []

RULES = ("B3/S23", "B36/S23", "B2/S", "B3678/S34678", "B1357/S1357", "B0123478/S01234678",
         "B368/S245", "B8/S8", "B0/S8", "B1/S1", "B34/S34", "B45678/S2345",
         "B/S012345678", "B012345678/S012345678")
SIZES = ((64, 3), (64, 4), (128, 77), (192, 64), (320, 257))


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "ok  " if condition else "FAIL"
    print(f"{status} {name}" + (f"   {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def reference_step(cells: np.ndarray, birth, survive) -> np.ndarray:
    """The textbook rule, one byte per cell -- slow, obviously right."""
    n = sum(np.roll(np.roll(cells, dy, 0), dx, 1)
            for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dy, dx) != (0, 0))
    born = np.isin(n, list(birth)) & (cells == 0)
    keep = np.isin(n, list(survive)) & (cells == 1)
    return (born | keep).astype(np.uint8)


@contextmanager
def tuning(threads: int | None = None, min_cells: int | None = None,
           per_thread: int | None = None, band_words: int | None = None):
    """Change the engine's knobs for a while and always put them back."""
    saved = (life.THREAD_MIN_CELLS, life.CELLS_PER_THREAD, life.BAND_WORDS)
    try:
        if threads is not None:
            life.set_threads(threads)
        if min_cells is not None:
            life.THREAD_MIN_CELLS = min_cells
        if per_thread is not None:
            life.CELLS_PER_THREAD = per_thread
        if band_words is not None:
            life.BAND_WORDS = band_words
        yield
    finally:
        life.THREAD_MIN_CELLS, life.CELLS_PER_THREAD, life.BAND_WORDS = saved
        life.set_threads(life.DEFAULT_THREADS)


def forced_threads(threads: int, band_rows: int, words: int):
    """Threads on however small a world, with bands of ``band_rows`` rows."""
    return tuning(threads=threads, min_cells=0, per_thread=1, band_words=band_rows * words)


def run_against_reference(world: World, cells: np.ndarray, generations: int) -> str | None:
    """Step both; the first disagreement (cells or any StepStats field), or None."""
    birth, survive = world.birth, world.survive
    for gen in range(generations):
        before = cells
        stats = world.step()
        cells = reference_step(cells, birth, survive)
        born = int(((cells == 1) & (before == 0)).sum())
        died = int(((cells == 0) & (before == 1)).sum())
        if not np.array_equal(world.to_array(), cells):
            return f"cells differ at generation {gen + 1}"
        if (stats.generation, stats.population, stats.born, stats.died) != (
                world.generation, int(cells.sum()), born, died):
            return f"stats {stats} at generation {gen + 1}, expected {int(cells.sum())} alive +{born} -{died}"
        if world.population != int(cells.sum()):
            return f"world.population {world.population} at generation {gen + 1}"
    return None


def soup(world: World, rng: np.random.Generator, density: float = 0.35) -> np.ndarray:
    cells = (rng.random((world.h, world.w)) < density).astype(np.uint8)
    world.load_array(cells)
    return cells


# ==================================================================== rules ===
def test_rule_notations() -> None:
    for text, expected in (("23/3", "B3/S23"), ("/3", "B3/S"), ("23/36", "B36/S23"), ("23/", "B/S23"),
                           ("B3/S23:T0", "B3/S23"), ("B3/S23:P100,50", "B3/S23"),
                           ("b36s23:T200,100", "B36/S23"), ("23/3:T0,0", "B3/S23"),
                           ("Life", "B3/S23"), ("conway", "B3/S23"), ("LifeHistory", "B3/S23"),
                           ("LIFEHISTORY", "B3/S23"), ("S32/B3", "B3/S23"), ("B3/S23", "B3/S23")):
        try:
            got = normalise_rule(text)
        except ValueError as exc:
            got = f"ValueError: {exc}"
        check(f"rule {text!r} reads as {expected}", got == expected, got)
    for bad in ("", "X3", "B3/Snope", "B9/S23", "B3", "/", "3", "23/9", "2a/3", ":T0", "Lifee",
                "23/3/1", None):
        try:
            parse_rule(bad)
            check(f"rule {bad!r} rejected", False)
        except ValueError:
            check(f"rule {bad!r} rejected", True)


def test_rule_compiler() -> None:
    rng = np.random.default_rng(7)
    wrong = []
    for _ in range(300):
        birth = frozenset(int(n) for n in np.nonzero(rng.random(9) < 0.4)[0])
        survive = frozenset(int(n) for n in np.nonzero(rng.random(9) < 0.4)[0])
        if not birth and not survive:
            continue
        program = life._compile_rule(birth, survive)      # checks itself; this checks it again
        table = life._evaluate(program)
        for i in range(32):
            n = (i >> 1 & 1) + 2 * (i >> 2 & 1) + 2 * (i >> 3 & 1) + 4 * (i >> 4 & 1)
            if n <= 8 and bool(table >> i & 1) != (n in (survive if i & 1 else birth)):
                wrong.append(life.format_rule(birth, survive))
                break
    check("300 random rules compile to programs that give the right answer for every count",
          not wrong, str(wrong[:3]))
    calls = {rule: len(life._compile_rule(*parse_rule(rule)).code) for rule in RULES}
    check("Conway compiles to at most five numpy calls", calls["B3/S23"] <= 5, str(calls["B3/S23"]))
    check("no listed rule needs more than a dozen", max(calls.values()) <= 12,
          ", ".join(f"{rule} {n}" for rule, n in calls.items()))


# =================================================================== engine ===
def test_matches_reference_inline() -> None:
    rng = np.random.default_rng(3)
    bad = []
    with tuning(threads=1):
        for rule in RULES:
            for width, height in SIZES:
                world = World(width, height, rule)
                problem = run_against_reference(world, soup(world, rng), 8)
                if problem:
                    bad.append((rule, width, height, problem))
    check("one band, inline: every rule matches the reference on every size", not bad, str(bad[:2]))


def test_matches_reference_banded() -> None:
    rng = np.random.default_rng(4)
    bad = []
    for band_rows in (1, 2, 5, 16):
        for rule in ("B3/S23", "B36/S23", "B0123478/S01234678", "B368/S245", "B0/S8", "B1357/S1357"):
            for width, height in SIZES:
                world = World(width, height, rule)
                with tuning(threads=1, band_words=band_rows * world.words):
                    problem = run_against_reference(world, soup(world, rng), 6)
                if problem:
                    bad.append((band_rows, rule, width, height, problem))
    check("many small bands, inline: seams, wrapped edge bands and short last bands", not bad, str(bad[:2]))
    world = World(128, 77)
    with tuning(threads=1, band_words=5 * world.words):
        world.step()
        bands = list(world._bands)
    check("a 77-row world in 5-row bands ends with a 2-row band",
          len(bands) == 16 and bands[-1] == (75, 77) and all(y1 - y0 == 5 for y0, y1 in bands[:-1]),
          str(bands[-2:]))


def test_matches_reference_threaded() -> None:
    rng = np.random.default_rng(5)
    bad = []
    used = set()
    for threads in (2, 3, 4):
        for band_rows in (1, 3, 7):
            for rule in RULES:
                for width, height in SIZES:
                    world = World(width, height, rule)
                    with forced_threads(threads, band_rows, world.words):
                        problem = run_against_reference(world, soup(world, rng), 4)
                        used.add(len(world._slots))
                    if problem:
                        bad.append((threads, band_rows, rule, width, height, problem))
    check("threads forced onto small worlds: every rule matches the reference", not bad, str(bad[:2]))
    check("and those worlds really ran on several threads", max(used) == 4 and 2 in used, str(sorted(used)))


def test_step_stats_exact() -> None:
    rng = np.random.default_rng(6)
    world = World(320, 257, seed=9)
    cells = soup(world, rng, 0.3)
    problems = []
    with forced_threads(4, 9, world.words):
        for round_ in range(6):
            problem = run_against_reference(world, cells, 5)
            if problem:
                problems.append((round_, problem))
                break
            # An edit between generations: population must follow it exactly.
            art = (rng.random((20, 30)) < 0.5).astype(np.uint8)
            x, y = int(rng.integers(0, world.w)), int(rng.integers(0, world.h))
            world.put_region(art, x, y, ("or", "erase", "replace")[round_ % 3])
            cells = world.to_array()
    check("StepStats stay exact across generations and edits (born, died, population, generation)",
          not problems, str(problems[:1]))
    empty = World(256, 64)
    with forced_threads(3, 4, empty.words):
        stats = [empty.step() for _ in range(3)]
    check("an empty world reports nothing born, nothing died",
          all((s.population, s.born, s.died) == (0, 0, 0) for s in stats) and stats[-1].generation == 3)


def test_threaded_matches_inline() -> None:
    inline = World(1024, 1024, seed=21)
    inline.seed_soup(0.3)
    threaded = World(1024, 1024, seed=21)
    threaded.seed_soup(0.3)
    same_start = np.array_equal(inline.bits, threaded.bits)
    with tuning(threads=1):
        a = [inline.step() for _ in range(200)]
    with forced_threads(4, 16, threaded.words):
        b = [threaded.step() for _ in range(200)]
        slots = len(threaded._slots)
    check("200 generations of a 1024x1024 soup: threaded and inline agree bit for bit",
          same_start and np.array_equal(inline.bits, threaded.bits) and a == b,
          f"{slots} threads, {inline.population} alive")


def test_step_from_a_worker_thread() -> None:
    world = World(512, 256, seed=2)
    world.seed_soup(0.3)
    expected = World(512, 256, seed=2)
    expected.seed_soup(0.3)
    with forced_threads(4, 4, world.words):
        future = life._executor().submit(lambda: [world.step() for _ in range(5)])
        try:
            future.result(timeout=20)
            finished = True
        except Exception as exc:                        # noqa: BLE001 - a timeout is the failure
            finished = False
            print(f"   {type(exc).__name__}: {exc}")
    with tuning(threads=1):
        for _ in range(5):
            expected.step()
    check("a world stepped from one of the engine's own threads runs inline instead of waiting forever",
          finished and np.array_equal(world.bits, expected.bits))


def test_pool_closed_mid_step() -> None:
    # set_threads() on another thread can shut the pool down between step()
    # fetching it and handing it work; submit() then raises.  The step must
    # still finish, working the bands itself.
    world = World(512, 96, seed=8)
    world.seed_soup(0.3)
    expected = World(512, 96, seed=8)
    expected.seed_soup(0.3)
    stats: list = []
    with forced_threads(4, 4, world.words):
        closed = life._executor()
        closed.shutdown(wait=True)
        life._pool = closed                             # what step() sees in that window
        try:
            stats = [world.step() for _ in range(5)]
            finished = True
        except RuntimeError as exc:
            finished = False
            print(f"   {type(exc).__name__}: {exc}")
        finally:
            life._pool = None
    with tuning(threads=1):
        wanted = [expected.step() for _ in range(5)]
    check("a step whose pool was closed by set_threads() on another thread still finishes, exactly",
          finished and np.array_equal(world.bits, expected.bits) and stats == wanted)


def test_set_threads() -> None:
    world = World(2048, 1024)                          # 2 M cells: just at the threading threshold
    with tuning(threads=1):
        world.step()
        off = len(world._slots)
    with tuning(threads=4):
        world.step()
        on = len(world._slots)
        small = World(1280, 720)
        small.step()
        below = len(small._slots)
    check("set_threads(1) turns threading off", off == 1)
    check("a world over THREAD_MIN_CELLS uses threads", on >= 2, f"{on} threads")
    check("a small world stays inline", below == 1)


# ============================================================ state & memory ===
def test_restore_across_shapes() -> None:
    world = World(128, 64, rule="B36/S23", seed=4)
    world.seed_soup(0.3)
    for _ in range(3):
        world.step()
    snap = world.snapshot()
    world.resize(256, 100)
    world.set_rule("B3/S23")
    world.seed_soup(0.2)
    for _ in range(4):
        world.step()
    epoch, version = world.epoch, world.version
    restored = world.restore(snap)
    check("restore takes a snapshot of another size", restored and (world.w, world.h) == (128, 64)
          and world.bits.shape == snap[0].shape, f"{world.w}x{world.h}")
    check("with its exact cells, generation and rule", np.array_equal(world.bits, snap[0])
          and world.generation == snap[1] and world.rule == "B36/S23")
    check("and marks the history as broken", world.epoch > epoch and world.version > version)
    check("the population is recounted", world.population == int(np.bitwise_count(snap[0]).sum()))
    cells = world.to_array()
    with forced_threads(4, 5, world.words):
        problem = run_against_reference(world, cells, 5)
    check("the restored world steps correctly (new scratch for the new size)", problem is None, str(problem))
    big = World(512, 300)
    big.restore(snap)
    check("a bigger world shrinks back to the snapshot", (big.w, big.h) == (128, 64)
          and np.array_equal(big.bits, snap[0]))
    same = World(128, 64)
    before = same.bits
    check("a snapshot of the same size is copied in place", same.restore(snap) and same.bits is before)
    other = World(256, 100, rule="B36/S23", seed=3)
    other.seed_soup(0.3)
    kept = (other.w, other.h, other.bits.copy(), other.generation, other.rule, other.epoch, other.population)
    try:
        other.restore((snap[0], 7, "B9/S23"))
        rejected = False
    except ValueError:
        rejected = True
    check("a snapshot with a bad rule is refused before anything changes", rejected
          and (other.w, other.h) == kept[:2] and np.array_equal(other.bits, kept[2])
          and (other.generation, other.rule, other.epoch, other.population) == kept[3:])


def test_resize_semantics() -> None:
    world = World(128, 64, seed=1)
    world.seed_soup(0.4)
    world.step()
    epoch = world.epoch
    world.resize(128, 64)
    check("resizing to the same size changes nothing", world.epoch == epoch and world.population > 0)
    world.resize(200, 50)
    check("resizing clears the world and bumps the epoch", (world.w, world.h) == (256, 50)
          and world.population == 0 and not world.bits.any() and world.epoch == epoch + 1)
    check("and drops the old scratch", world._slots == [] and world._layout_key is None)


def test_seed_soup_unchanged() -> None:
    # 4096x600 fills in three blocks of rows (256, 256, 88); the others in one.
    for width, height in ((128, 77), (1280, 720), (4096, 600)):
        world = World(width, height, seed=12)
        world.seed_soup(0.22)
        expected = life.pack(np.random.default_rng(12).random((world.h, world.w)) < 0.22)
        check(f"a {width}x{height} soup is the same cells it always was for the seed",
              np.array_equal(world.bits, expected))


def world_bytes(world: World) -> tuple[int, int, int]:
    """(bits, next generation, scratch) bytes held by a world."""
    scratch = 0
    for slot in world._slots:
        scratch += sum(a.nbytes for a in (slot.src, slot.x, slot.we, slot.carry, slot.counts))
        scratch += sum(a.nbytes for a in slot.extra)
    return world.bits.nbytes, world._next.nbytes, scratch


def test_memory() -> None:
    world = World(7680, 8640)
    world.step()
    bits, nxt, scratch = world_bytes(world)
    largest = max(max(a.nbytes for a in (s.src, s.x, s.we, s.carry, s.counts)) for s in world._slots)
    arrays = [k for k, v in vars(world).items() if isinstance(v, np.ndarray)]
    mib = 1 << 20
    check("a 7680x8640 world holds its bits, the next generation and band scratch only",
          sorted(arrays) == ["_next", "bits"] and largest < bits // 2,
          f"{bits / mib:.1f} + {nxt / mib:.1f} + {scratch / mib:.1f} MiB scratch "
          f"({len(world._slots)} bands at once) = {(bits + nxt + scratch) / mib:.1f} MiB")
    check("all its band scratch together is less than two copies of the world", scratch < 2 * bits,
          f"{scratch / bits:.2f} x the world (the old engine kept ~20 world-sized temporaries)")


# =============================================================== benchmark ===
def time_steps(world: World, generations: int, repeats: int = 3) -> float:
    world.step()
    best = float("inf")
    for _ in range(repeats):
        started = time.perf_counter()
        for _ in range(generations):
            world.step()
        best = min(best, (time.perf_counter() - started) / generations)
    return best * 1000


def test_benchmark() -> None:
    print(f"   numpy {np.__version__}, {life.DEFAULT_THREADS} threads by default, threads from "
          f"{life.THREAD_MIN_CELLS:,} cells, bands of {life.BAND_WORDS:,} words")
    fast = None
    for width, height, generations in ((1280, 720, 300), (3840, 2160, 40), (7680, 8640, 8)):
        world = World(width, height, seed=1)
        rng = np.random.default_rng(1)
        bits = rng.integers(0, 1 << 64, world.bits.shape, dtype=np.uint64, endpoint=False)
        world.restore((bits & rng.integers(0, 1 << 64, world.bits.shape, dtype=np.uint64), 0, world.rule))
        with tuning(threads=1):
            inline = time_steps(world, generations)
        threaded = time_steps(world, generations)          # the default configuration
        threads = len(world._slots)
        if width == 1280:
            fast = min(inline, threaded)
        print(f"   {width}x{height}: {inline:7.3f} ms/gen inline, {threaded:7.3f} ms/gen on "
              f"{threads} thread{'s' if threads > 1 else ''}")
    check("a 1280x720 generation takes under 2 ms", fast is not None and fast < 2.0, f"{fast:.3f} ms")


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
