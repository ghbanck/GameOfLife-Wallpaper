"""The simulation: an edgeless Life world, stepped 64 cells at a time.

The world is a torus, so panning the camera never reaches a boundary -- it
wraps, which is what makes the space feel infinite while keeping the cost of a
generation fixed and predictable.

Cells are stored as bits, 64 to a ``uint64`` word, and a generation is a few
dozen bitwise operations: the eight neighbours of every cell are summed with
ripple-carry adders built out of AND/XOR, so each numpy call moves 64 cells at
once.  That is roughly twenty times faster than summing a byte per cell, which
is what makes a skip of a million generations practical and keeps an idle
wallpaper under a percent of one core.

A generation is worked out in horizontal bands of rows, each with its own
band-sized scratch, rather than as whole-array passes: the temporaries of a
huge world then stay in the CPU caches and never cost a world-sized block of
memory each.  A band reads one row above and below itself and writes only its
own rows of the next generation, so bands are independent and a large world
runs them on a few threads -- numpy lets go of the GIL inside its loops.  The
rule itself is compiled to the shortest formula over the neighbour-count bits
that a small search can find, so Conway costs five numpy calls, not seventeen.

The world only ever changes by the rule or by the user: nothing here adds
cells on its own, so a pattern placed on a clean board runs exactly as Life
says it should.
"""

from __future__ import annotations

import heapq
import os
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations
from typing import NamedTuple

import numpy as np

WORD_BITS = 64
_TOP = np.uint64(63)
_HIGH = np.uint64(1 << 63)
_ALL = np.uint64((1 << 64) - 1)

# -- tuning --------------------------------------------------------------------
# Measured on a Ryzen 5 7600X with numpy 2.5, whose bitwise loops run about a
# word per cycle wherever the data sits: what matters is the number of passes,
# the fixed price of each numpy call, and -- with threads -- how often the GIL
# changes hands, which is once per call.  tests/test_engine.py prints the numbers.
DEFAULT_THREADS = min(4, max(1, (os.cpu_count() or 2) // 2))
# Below this a generation takes about 0.3 ms inline, and waking helper threads
# (tens of microseconds each) gains too little to be worth a second core.
THREAD_MIN_CELLS = 2_000_000
# Each thread wants about this much of the world: with less, a band's numpy
# calls and GIL hand-overs cost more than the thread saves.
CELLS_PER_THREAD = 1_000_000
# Words per band (256 KB per scratch plane, ~2.6 MB of scratch per band).
# Smaller bands would sit in L2 but make more numpy calls, and with threads
# every call is a GIL hand-over: 7680x8640 on 4 threads takes 2.9 ms a
# generation with these, 3.6 ms with half the size, 5.9 ms with a quarter.
BAND_WORDS = 32_768

_THREAD_PREFIX = "golwall-life"
_threads = DEFAULT_THREADS
_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()


def set_threads(n: int) -> None:
    """Step large worlds on ``n`` threads (the caller counts as one); 1 or less turns threading off."""
    global _threads, _pool
    with _pool_lock:
        _threads = max(1, int(n))
        old, _pool = _pool, None
    if old is not None:
        old.shutdown(wait=False)                # idle workers just exit


def _executor() -> ThreadPoolExecutor:
    # Created on first use and shared by every world.  Its workers sit idle
    # between generations and are joined at interpreter exit, which returns at
    # once because they are only ever waiting for work.
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=max(1, _threads - 1),
                                       thread_name_prefix=_THREAD_PREFIX)
        return _pool


def _in_worker() -> bool:
    # A world stepped from inside one of our own workers must not queue work
    # behind itself: with every worker busy that would never finish.
    return threading.current_thread().name.startswith(_THREAD_PREFIX)


# -- rules ---------------------------------------------------------------------
_RULE_NAMES = {"LIFE": "B3/S23", "CONWAY": "B3/S23", "LIFEHISTORY": "B3/S23"}


def parse_rule(rule: str) -> tuple[frozenset[int], frozenset[int]]:
    """Parse a rule string such as ``B3/S23`` (or ``b3s23``) into (birth, survive).

    Old RLE files write the same rule as ``23/3`` -- survival first, no
    letters -- and Golly adds a topology after a colon (``B3/S23:T0``).  The
    world here is always a torus of its own size, so that part is ignored.
    """
    text = str(rule).split(":", 1)[0].upper().replace(" ", "")
    text = _RULE_NAMES.get(text, text)
    if not text:
        raise ValueError("empty rule")
    if text.count("/") == 1 and "B" not in text and "S" not in text:
        survive_digits, birth_digits = text.split("/")
        text = f"B{birth_digits}/S{survive_digits}"
    birth: frozenset[int] | None = None
    survive: frozenset[int] | None = None
    for part in text.replace("S", "/S").split("/"):
        if not part:
            continue
        kind, digits = part[0], part[1:]
        if kind not in "BS" or not all(d in "012345678" for d in digits):
            raise ValueError(f"bad rule segment {part!r} in {rule!r}")
        counts = frozenset(int(d) for d in digits)
        if kind == "B":
            birth = counts
        else:
            survive = counts
    if birth is None or survive is None:
        raise ValueError(f"a rule needs both a B and an S part: {rule!r}")
    if not birth and not survive:
        raise ValueError(f"empty rule: {rule!r}")
    return birth, survive


def format_rule(birth, survive) -> str:
    return "B" + "".join(str(n) for n in sorted(birth)) + "/S" + "".join(str(n) for n in sorted(survive))


def normalise_rule(rule: str) -> str:
    return format_rule(*parse_rule(rule))


# -- compiling a rule ------------------------------------------------------------
# The adder leaves each cell's neighbour count as n = s0 + 2x + 2c + 4y, four
# bit planes.  With the cell itself (a) that makes the rule a boolean function
# of five inputs, small enough to search for a short formula instead of testing
# every count in turn: Conway comes out as ``~y & (x ^ c) & (s0 | a)``, five
# numpy calls where testing counts took seventeen.  A truth table is a 32-bit
# int whose bit i is the output for input combination i (a is bit 0 of i).
_INPUTS = ("a", "s0", "x", "c", "y")
_FULL = (1 << 32) - 1
_VAR = tuple(sum(1 << i for i in range(32) if i >> v & 1) for v in range(5))
_BUSY = object()
_OUT = len(_INPUTS)                              # register numbers: inputs, out, then temps
_UFUNCS = {"and": np.bitwise_and, "or": np.bitwise_or, "xor": np.bitwise_xor, "not": np.invert}


def _cofactor(t: int, v: int, bit: int) -> int:
    """The table with input ``v`` fixed at ``bit`` (so it no longer depends on it)."""
    shift = 1 << v
    if bit:
        t &= _VAR[v]
        return t | (t >> shift)
    t &= _VAR[v] ^ _FULL
    return t | (t << shift)


def _synth(t: int, memo: dict) -> tuple[int, object]:
    """(cost, formula) of a short formula for table ``t``.

    Tries a disjoint AND/OR/XOR split of the inputs, then a Shannon split on
    each input with the cheap special cases of a multiplexer.  Formulas are
    input names, ``"0"``/``"1"``, or ``(op, operand, ...)`` tuples.
    """
    hit = memo.get(t)
    if hit is not None and hit is not _BUSY:
        return hit
    if t in (0, _FULL):
        return 0, "1" if t else "0"
    for v, name in enumerate(_INPUTS):
        if t == _VAR[v]:
            return 0, name
        if t == _VAR[v] ^ _FULL:
            return 1, ("not", name)
    memo[t] = _BUSY                               # a complement may not lean on this one
    best: list = [None]

    def offer(cost: int, formula) -> None:
        if best[0] is None or cost < best[0][0]:
            best[0] = (cost, formula)

    support = [v for v in range(5) if _cofactor(t, v, 0) != _cofactor(t, v, 1)]
    for v in support:
        name, inverse = _INPUTS[v], ("not", _INPUTS[v])
        f0, f1 = _cofactor(t, v, 0), _cofactor(t, v, 1)
        if f0 == 0:
            cost, e = _synth(f1, memo)
            offer(cost + 1, ("and", e, name))
        elif f1 == _FULL:
            cost, e = _synth(f0, memo)
            offer(cost + 1, ("or", e, name))
        elif f1 == f0 ^ _FULL:
            cost, e = _synth(f0, memo)
            offer(cost + 1, ("xor", e, name))
        elif f1 == 0:
            cost, e = _synth(f0, memo)
            offer(cost + 2, ("and", e, inverse))
        elif f0 == _FULL:
            cost, e = _synth(f1, memo)
            offer(cost + 2, ("or", e, inverse))
        else:
            (c0, e0), (c1, e1), (cd, ed) = _synth(f0, memo), _synth(f1, memo), _synth(f0 ^ f1, memo)
            offer(c0 + cd + 2, ("xor", e0, ("and", ed, name)))
            offer(c1 + cd + 3, ("xor", e1, ("and", ed, inverse)))
            if not f0 & ~f1 & _FULL:
                offer(c0 + c1 + 2, ("or", e0, ("and", e1, name)))
            if not f1 & ~f0 & _FULL:
                offer(c0 + c1 + 3, ("or", e1, ("and", e0, inverse)))
    corner = t
    for v in support:
        corner = _cofactor(corner, v, 0)
    first, others = support[0], support[1:]
    for size in range(len(others)):
        for extra in combinations(others, size):
            group = (first,) + extra
            rest = [v for v in support if v not in group]
            g_any = g_all = g_zero = t                # functions of the group alone
            for v in rest:
                g_any = _cofactor(g_any, v, 0) | _cofactor(g_any, v, 1)
                g_all = _cofactor(g_all, v, 0) & _cofactor(g_all, v, 1)
                g_zero = _cofactor(g_zero, v, 0)
            h_any = h_all = h_zero = t                # functions of the rest alone
            for v in group:
                h_any = _cofactor(h_any, v, 0) | _cofactor(h_any, v, 1)
                h_all = _cofactor(h_all, v, 0) & _cofactor(h_all, v, 1)
                h_zero = _cofactor(h_zero, v, 0)
            h_zero ^= corner
            for op, g, h, joined in (("and", g_any, h_any, g_any & h_any),
                                     ("or", g_all, h_all, g_all | h_all),
                                     ("xor", g_zero, h_zero, g_zero ^ h_zero)):
                if joined == t:
                    (cg, eg), (ch, eh) = _synth(g, memo), _synth(h, memo)
                    offer(cg + ch + 1, (op, eg, eh))
    if memo.get(t ^ _FULL) is not _BUSY:
        cost, e = _synth(t ^ _FULL, memo)
        offer(cost + 1, ("not", e))
    memo[t] = best[0]
    return best[0]


def _table(formula) -> int:
    """The truth table a formula computes."""
    if isinstance(formula, str):
        if formula in ("0", "1"):
            return _FULL if formula == "1" else 0
        return _VAR[_INPUTS.index(formula)]
    if formula[0] == "not":
        return _table(formula[1]) ^ _FULL
    left, right = _table(formula[1]), _table(formula[2])
    return {"and": left & right, "or": left | right, "xor": left ^ right}[formula[0]]


class _Program(NamedTuple):
    """A rule as a list of numpy calls over registers (inputs, ``out``, temps)."""
    code: tuple            # (op, dst, left, right); right is -1 for "not"
    result: str            # the formula's leaf when there is no code: an input, "0" or "1"
    planes: frozenset      # the neighbour planes the code reads
    temps: int

    @property
    def calls(self) -> tuple:
        """``code`` with the numpy function in place of each op's name."""
        return tuple((_UFUNCS[op], dst, left, right) for op, dst, left, right in self.code)


def _linearise(formula) -> _Program:
    """Number the formula's distinct sub-results and give each a register."""
    order: list = []                            # (op, operands) in evaluation order
    index: dict[int, int] = {}                  # truth table -> position, so repeats are shared

    def visit(e):
        if isinstance(e, str):
            return e
        t = _table(e)
        if t not in index:
            operands = tuple(visit(k) for k in e[1:])
            index[t] = len(order)
            order.append((e[0], operands))
        return index[t]

    root = visit(formula)
    if isinstance(root, str):
        planes = frozenset({root} & set(_INPUTS[1:]))
        return _Program((), root, planes, 0)
    last = {k: pos for pos, (_, operands) in enumerate(order) for k in operands if isinstance(k, int)}
    free: list[int] = []
    reg: dict[int, int] = {}
    code, temps = [], 0
    for pos, (op, operands) in enumerate(order):
        regs = [reg[k] if isinstance(k, int) else _INPUTS.index(k) for k in operands]
        for k in set(operands):
            if isinstance(k, int) and last[k] == pos:
                heapq.heappush(free, reg[k])      # an operand's last use: its buffer can take the result
        if pos == root:
            dst = _OUT
        elif free:
            dst = heapq.heappop(free)
        else:
            dst, temps = _OUT + 1 + temps, temps + 1
        reg[pos] = dst
        code.append((op, dst, regs[0], regs[1] if len(regs) > 1 else -1))
    planes = frozenset(_INPUTS[r] for _, _, *rs in code for r in rs if 0 < r < _OUT)
    return _Program(tuple(code), "", planes, temps)


def _evaluate(program: _Program) -> int:
    """Run a program on truth tables: its output for every input combination."""
    if not program.code:
        return _table(program.result)
    regs = list(_VAR) + [0] * (1 + program.temps)
    for op, dst, left, right in program.code:
        if op == "not":
            regs[dst] = regs[left] ^ _FULL
        elif op == "and":
            regs[dst] = regs[left] & regs[right]
        elif op == "or":
            regs[dst] = regs[left] | regs[right]
        else:
            regs[dst] = regs[left] ^ regs[right]
    return regs[_OUT]


@lru_cache(maxsize=64)
def _compile_rule(birth: frozenset[int], survive: frozenset[int]) -> _Program:
    care = target = 0
    for i in range(32):
        n = (i >> 1 & 1) + 2 * (i >> 2 & 1) + 2 * (i >> 3 & 1) + 4 * (i >> 4 & 1)
        if n <= 8:
            care |= 1 << i
            if n in (survive if i & 1 else birth):
                target |= 1 << i
    # n = 9 never happens, so either answer will do there; keep the shorter program.
    memo: dict = {}
    best: _Program | None = None
    for fill in range(4):
        t = target
        for i in range(32):
            if not care >> i & 1 and fill >> (i & 1) & 1:
                t |= 1 << i
        program = _linearise(_synth(t, memo)[1])
        if best is None or len(program.code) < len(best.code):
            best = program
    if (_evaluate(best) ^ target) & care:
        raise AssertionError(f"rule B{sorted(birth)}/S{sorted(survive)} compiled wrongly")
    return best


# -- packing --------------------------------------------------------------------
def blit_wrapped(src: np.ndarray, dst: np.ndarray, x0: int, y0: int) -> None:
    """Copy the ``dst``-sized window at (x0, y0) out of the toroidal ``src``."""
    H, W = src.shape[:2]
    h, w = dst.shape[:2]
    x0 %= W
    y0 %= H
    h1, w1 = min(h, H - y0), min(w, W - x0)
    dst[:h1, :w1] = src[y0:y0 + h1, x0:x0 + w1]
    if w1 < w:
        dst[:h1, w1:] = src[y0:y0 + h1, :w - w1]
    if h1 < h:
        dst[h1:, :w1] = src[:h - h1, x0:x0 + w1]
        if w1 < w:
            dst[h1:, w1:] = src[:h - h1, :w - w1]


def pack(cells: np.ndarray) -> np.ndarray:
    """(h, w) array of 0/1 -> (h, w // 64) uint64, cell ``i`` in bit ``i % 64``."""
    packed = np.packbits(np.asarray(cells, bool), axis=1, bitorder="little")
    return np.ascontiguousarray(packed).view("<u8")


def unpack(bits: np.ndarray) -> np.ndarray:
    """The inverse of :func:`pack`: (h, words) uint64 -> (h, words * 64) uint8."""
    raw = np.ascontiguousarray(bits).view(np.uint8)
    return np.unpackbits(raw, axis=1, bitorder="little")


# -- band stepping ------------------------------------------------------------------
# numpy's bitwise loops run about a word every cycle or two, so a band costs its
# word count times the number of passes, plus a fixed price per numpy call (and
# a GIL hand-over when threads share the work).  Passes that do the same thing
# to different data therefore go through numpy as one call: both shifted copies
# of the band come out of one broadcast, both carries out of another, one OR
# joins each copy to its carry, and the two full adders of the vertical sum
# (the ones and the twos) run side by side as (2, n) operations.
_SHIFT_MUL = np.array([[2], [1 << 63]], np.uint64)     # a << 1 (west), a << 63 (east's carry)
_SHIFT_SHR = np.array([[63], [1]], np.uint64)          # a >> 63 (west's carry), a >> 1 (east)
_S0_ONLY = frozenset({"s0"})


def _pair(buf: np.ndarray, start: int, stride: int, n: int) -> np.ndarray:
    """Two ``n``-word runs of ``buf``, at ``start`` and ``start + stride``, as one (2, n) view."""
    if not (0 <= start and 0 < stride and start + stride + n <= buf.size):
        raise ValueError("band view out of range")
    item = buf.itemsize
    return np.lib.stride_tricks.as_strided(buf[start:], (2, n), (stride * item, item))


class _Views(NamedTuple):
    muls: np.ndarray       # (2, n): a << 1 and a << 63, written by one broadcast
    shrs: np.ndarray       # (2, n): a >> 63 and a >> 1
    fix_w: np.ndarray      # a row's first word takes its carry from the row's last word...
    fix_e: np.ndarray      # ...and its last word from its first
    left: np.ndarray       # (2, n) operands of the one OR that makes west and east
    right: np.ndarray
    west: np.ndarray       # west and east; afterwards the row sums h0 and h1
    east: np.ndarray
    we: np.ndarray         # both, as (2, n)
    g0: np.ndarray         # the two-cell sums of every row
    g1: np.ndarray
    up: np.ndarray         # (2, m) sums of the rows above, beside and below each band row
    mid: np.ndarray
    down: np.ndarray
    t: np.ndarray          # (2, m) scratch of the adder
    carry: np.ndarray      # (2, m): c, y
    planes: tuple          # the rule's inputs after a: s0, x, c, y
    temps: tuple           # buffers the rule's program may use
    counts: np.ndarray     # one uint64 per word: live cells in byte 0, changed cells in byte 4
    lo: np.ndarray
    hi: np.ndarray


class _Slot:
    """The scratch of one band at a time: never shared by two bands running at once."""

    def __init__(self, rows: int, words: int, temps: int) -> None:
        n, m = (rows + 2) * words, rows * words
        self.words = words
        self.src = np.empty((rows + 2, words), np.uint64)    # wrapped copy for the edge bands
        self.x = np.empty(4 * n + 2, np.uint64)              # the shifts, then g and the adder's t
        self.we = np.empty(2 * n, np.uint64)                 # west and east, then h
        self.carry = np.empty(2 * m, np.uint64)
        self.extra = [np.empty(m, np.uint64) for _ in range(max(0, temps - 4))]
        self.counts = np.zeros(m, np.uint64)                 # only bytes 0 and 4 are ever written
        self._views: dict[int, _Views] = {}

    def views(self, rows: int) -> _Views:
        """Views sized for a band of ``rows`` rows (the last band may be shorter)."""
        found = self._views.get(rows)
        if found is not None:
            return found
        w, x = self.words, self.x
        n, m = (rows + 2) * w, rows * w
        # x holds, in order: a spare word, a >> 63, a << 1, a << 63, a spare
        # word, a >> 1.  West is (a << 1) | (the word before's a >> 63) and
        # east is (a >> 1) | (the word after's a << 63): with this layout the
        # two ORs line up as one strided operation, and the spare words hold
        # the carries of the band's very first and very last word.
        shr_w, mul_w, mul_e, shr_e = 1, n + 1, 2 * n + 1, 3 * n + 2
        we = self.we[:2 * n].reshape(2, n)
        g = x[:2 * n].reshape(2, n)
        t = x[2 * n:2 * n + 2 * m].reshape(2, m)
        carry = self.carry[:2 * m].reshape(2, m)
        mid = g[:, w:w + m]
        counts = self.counts[:m]
        as_bytes = counts.view(np.uint8)
        found = _Views(
            muls=_pair(x, mul_w, mul_e - mul_w, n),
            shrs=_pair(x, shr_w, shr_e - shr_w, n),
            fix_w=x[shr_w - 1:shr_w + (rows + 1) * w:w],        # carries into each row's first word
            fix_e=x[mul_e + w:mul_e + n + 1:w],                  # ...and into its last word
            left=_pair(x, mul_w, shr_e - mul_w, n),
            right=_pair(x, shr_w - 1, mul_e + 1 - (shr_w - 1), n),
            west=we[0], east=we[1], we=we, g0=g[0], g1=g[1],
            up=we[:, :m], mid=mid, down=we[:, 2 * w:2 * w + m],
            t=t, carry=carry,
            planes=(mid[0], mid[1], carry[0], carry[1]),
            temps=(t[0], t[1], we[0, :m], we[1, :m]) + tuple(buf[:m] for buf in self.extra),
            counts=counts, lo=as_bytes[0::8], hi=as_bytes[4::8])
        self._views[rows] = found
        return found


def _step_band(program: _Program, calls: tuple, slot: _Slot, src: np.ndarray,
               out: np.ndarray) -> tuple[int, int]:
    """Write the next generation of a band into ``out``; returns (live, changed).

    ``src`` is the band with one row above and one below, contiguous, so the
    rows either side of every band row are plain offsets into it.
    """
    rows, w = out.shape
    v = slot.views(rows)
    flat = src.reshape(-1)
    outf = out.reshape(-1)
    a = flat[w:w + outf.size]
    xor, and_, or_ = np.bitwise_xor, np.bitwise_and, np.bitwise_or
    planes = program.planes
    if planes:
        full = planes != _S0_ONLY
        # west[i] is cell i-1 and east[i] cell i+1: every word shifted a bit
        # (multiplying is faster than numpy's left shift), with the bit carried
        # in from the word beside it -- which, for a row's first or last word,
        # is the other end of the same row, because the world wraps.
        np.multiply(flat[None], _SHIFT_MUL, out=v.muls)
        np.right_shift(flat[None], _SHIFT_SHR, out=v.shrs)
        np.right_shift(src[:, -1], _TOP, out=v.fix_w)
        np.multiply(src[:, 0], _HIGH, out=v.fix_e)
        or_(v.left, v.right, out=v.we)
        # Each row's three-cell sum (h1 h0) and, for the middle row, the
        # two-cell sum without the cell itself (g1 g0).
        xor(v.west, v.east, out=v.g0)
        if full:
            and_(v.west, v.east, out=v.g1)
        xor(v.g0, flat, out=v.west)                  # h0, over west now it is used up
        if full:
            and_(v.g0, flat, out=v.east)
            or_(v.east, v.g1, out=v.east)            # h1
            # (u1 u0) + (g1 g0) + (d1 d0), at most 8: a full adder over the
            # ones and one over the twos, side by side, leaving the count as
            # s0 + 2c + 2x + 4y.
            and_(v.up, v.mid, out=v.carry)
            xor(v.up, v.mid, out=v.t)
            xor(v.t, v.down, out=v.mid)              # s0, x -- over g, now used up
            and_(v.t, v.down, out=v.t)
            or_(v.carry, v.t, out=v.carry)           # c, y
        else:
            xor(v.up[0], v.mid[0], out=v.t[0])
            xor(v.t[0], v.down[0], out=v.mid[0])     # s0
    if calls:
        regs = (a,) + v.planes + (outf,) + v.temps
        for fn, dst, left, right in calls:
            if right < 0:
                fn(regs[left], out=regs[dst])
            else:
                fn(regs[left], regs[right], out=regs[dst])
    elif program.result == "0":
        outf.fill(0)
    elif program.result == "1":
        outf.fill(_ALL)
    else:
        np.copyto(outf, ((a,) + v.planes)[_INPUTS.index(program.result)])
    # Live and changed cells counted into two bytes of one uint64 per word, so
    # a single native sum gives both.
    np.bitwise_count(outf, out=v.lo)
    xor(outf, a, out=v.t[0])
    np.bitwise_count(v.t[0], out=v.hi)
    total = int(np.add.reduce(v.counts))
    return total & 0xFFFF_FFFF, total >> 32


@dataclass
class StepStats:
    generation: int
    population: int
    born: int
    died: int

    @property
    def changed(self) -> int:
        return self.born + self.died


class World:
    """An edgeless Life universe, stored one bit per cell.

    ``version`` goes up on every change, generation or edit, so a renderer can
    tell when to upload; ``epoch`` goes up when the history of the cells stops
    meaning anything (cleared, reseeded, loaded, undone) so trails and ages
    can be reset instead of being drawn across a jump.
    """

    def __init__(self, width: int, height: int, rule: str = "B3/S23",
                 seed: int | None = None) -> None:
        self.nprng = np.random.default_rng(seed)
        self.generation = 0
        self.version = 0
        self.epoch = 0
        self.population = 0
        self.set_rule(rule)
        self._allocate(width, height)

    # -- shape --------------------------------------------------------------
    @staticmethod
    def fit_width(width: int) -> int:
        """Widths are whole words: round up to a multiple of 64 cells."""
        return max(WORD_BITS, -(-int(width) // WORD_BITS) * WORD_BITS)

    def _allocate(self, width: int, height: int) -> None:
        self.w = self.fit_width(width)
        self.h = max(3, int(height))
        self.words = self.w // WORD_BITS
        shape = (self.h, self.words)
        self.bits = np.zeros(shape, np.uint64)
        self._next = np.zeros(shape, np.uint64)
        # Band scratch is sized to a band, not the world, and made at the next step.
        self._slots: list[_Slot] = []
        self._bands: list[tuple[int, int]] = []
        self._layout_key: tuple | None = None
        self.population = 0

    def resize(self, width: int, height: int) -> None:
        if (self.fit_width(width), max(3, int(height))) == (self.w, self.h):
            return
        self._allocate(width, height)
        self.epoch += 1
        self._changed()

    @property
    def cells(self) -> int:
        return self.w * self.h

    # -- the rule -----------------------------------------------------------
    def set_rule(self, rule: str) -> None:
        birth, survive = parse_rule(rule)
        self.birth, self.survive = birth, survive
        self.rule = format_rule(birth, survive)
        self._program = _compile_rule(birth, survive)
        self._calls = self._program.calls

    # -- whole-world views --------------------------------------------------
    def to_array(self) -> np.ndarray:
        """Every cell as an (h, w) uint8 array of 0/1 -- a copy."""
        return unpack(self.bits)

    def load_array(self, cells: np.ndarray) -> None:
        cells = np.asarray(cells)
        h, w = min(cells.shape[0], self.h), min(cells.shape[1], self.w)
        full = np.zeros((self.h, self.w), np.uint8)
        full[:h, :w] = cells[:h, :w] != 0
        self.bits[:] = pack(full)
        self.epoch += 1
        self._changed()

    def _changed(self) -> None:
        self.version += 1
        self.population = int(np.bitwise_count(self.bits).sum())

    # -- editing ------------------------------------------------------------
    def _rows(self, y: int, count: int) -> np.ndarray:
        return (np.arange(count) + int(y)) % self.h

    def _cols(self, x: int, count: int) -> np.ndarray:
        return (np.arange(count) + int(x)) % self.w

    def get_region(self, x: int, y: int, width: int, height: int) -> np.ndarray:
        """The (height, width) block of cells with its top-left at (x, y), wrapping."""
        width, height = max(0, int(width)), max(0, int(height))
        if not width or not height:
            return np.zeros((height, width), np.uint8)
        block = unpack(self.bits[self._rows(y, height)])
        return block[:, self._cols(x, width)]

    def put_region(self, cells: np.ndarray, x: int, y: int, mode: str = "or") -> None:
        """Write a block of cells at (x, y), wrapping.

        ``or`` adds live cells, ``erase`` kills the cells that are set in the
        block, ``replace`` copies the block over whatever was there.
        """
        cells = np.asarray(cells)
        if cells.ndim != 2 or not cells.size:
            return
        cells = cells[:self.h, :self.w] != 0          # never wrap onto itself
        rows = self._rows(y, cells.shape[0])
        cols = self._cols(x, cells.shape[1])
        before = self.bits[rows]
        block = unpack(before)
        if mode == "or":
            block[:, cols] |= cells.view(np.uint8)
        elif mode == "erase":
            block[:, cols] &= (~cells).view(np.uint8)
        elif mode == "replace":
            block[:, cols] = cells
        else:
            raise ValueError(f"unknown mode {mode!r}")
        after = pack(block)
        self.bits[rows] = after
        # Recount only the rows touched: a brush stroke on a huge world should
        # not pay for counting every cell on every mouse move.
        self.population += int(np.bitwise_count(after).sum()) - int(np.bitwise_count(before).sum())
        self.version += 1

    def clear_region(self, x: int, y: int, width: int, height: int) -> None:
        width, height = min(int(width), self.w), min(int(height), self.h)
        if width > 0 and height > 0:
            self.put_region(np.zeros((height, width), np.uint8), x, y, "replace")

    def clear(self) -> None:
        self.bits[:] = 0
        self.epoch += 1
        self._changed()

    def seed_soup(self, density: float = 0.22,
                  region: tuple[int, int, int, int] | None = None) -> None:
        """Fill the world (or a wrapped sub-rectangle x0, y0, x1, y1) with noise."""
        density = float(density)
        if region is None:
            # A block of rows at a time, so a huge world never needs a float
            # per cell at once (half a gigabyte at 7680x8640).  The generator
            # fills row by row either way, so the cells are the same.
            block = max(1, (1 << 20) // self.w)
            for y in range(0, self.h, block):
                rows = min(block, self.h - y)
                self.bits[y:y + rows] = pack(self.nprng.random((rows, self.w)) < density)
            self.epoch += 1
            self._changed()
            return
        x0, y0, x1, y1 = (int(v) for v in region)
        width, height = min(x1 - x0, self.w), min(y1 - y0, self.h)
        if width <= 0 or height <= 0:
            return
        self.put_region(self.nprng.random((height, width)) < density, x0, y0, "replace")

    def stamp(self, art: np.ndarray, x: int, y: int, rotate: int = 0,
              flip: bool = False, erase: bool = False) -> None:
        """Draw a pattern with its top-left at world cell (x, y), wrapping."""
        if rotate:
            art = np.rot90(art, rotate % 4)
        if flip:
            art = np.fliplr(art)
        self.put_region(art, x, y, "erase" if erase else "or")

    # -- snapshots (undo, saving) ---------------------------------------------
    def snapshot(self) -> tuple[np.ndarray, int, str]:
        return self.bits.copy(), self.generation, self.rule

    def restore(self, snap: tuple[np.ndarray, int, str]) -> bool:
        """Put back a snapshot, taking its size if the world was resized since."""
        bits, generation, rule = snap
        bits = np.asarray(bits)
        if bits.ndim != 2 or bits.shape[0] < 3 or bits.shape[1] < 1:
            return False
        if rule != self.rule:
            self.set_rule(rule)                     # a bad rule raises before anything changes
        if bits.shape != self.bits.shape:
            self._allocate(bits.shape[1] * WORD_BITS, bits.shape[0])
        self.bits[:] = bits
        self.generation = int(generation)
        self.epoch += 1
        self._changed()
        return True

    # -- simulation ---------------------------------------------------------
    def _layout(self) -> tuple[list[_Slot], list[tuple[int, int]]]:
        """The bands of this world and one scratch slot per band that can run at once."""
        threads = 1
        if _threads > 1 and self.cells >= THREAD_MIN_CELLS and not _in_worker():
            threads = min(_threads, max(2, self.cells // max(1, CELLS_PER_THREAD)))
        key = (self.h, self.words, BAND_WORDS, threads, self._program.temps)
        if key != self._layout_key:
            rows = max(1, min(self.h, BAND_WORDS // self.words))
            count = max(-(-self.h // rows), min(threads, self.h))   # a band for every thread
            rows = -(-self.h // count)
            self._bands = [(y, min(y + rows, self.h)) for y in range(0, self.h, rows)]
            self._slots = []                        # let the old scratch go before the new
            self._slots = [_Slot(rows, self.words, self._program.temps)
                           for _ in range(min(threads, len(self._bands)))]
            self._layout_key = key
        return self._slots, self._bands

    def _work(self, a: np.ndarray, nxt: np.ndarray, todo: deque, slot: _Slot) -> tuple[int, int]:
        """Take bands off ``todo`` until none are left; returns (live, changed)."""
        h, program, calls = self.h, self._program, self._calls
        live = changed = 0
        while True:
            try:
                y0, y1 = todo.popleft()
            except IndexError:
                return live, changed
            if y0 and y1 < h:
                src = a[y0 - 1:y1 + 1]              # halo rows included, no copy
            else:
                src = slot.src[:y1 - y0 + 2]        # the first or last band: wrap round
                src[0] = a[y0 - 1]
                src[1:-1] = a[y0:y1]
                src[-1] = a[y1 % h]
            band_live, band_changed = _step_band(program, calls, slot, src, nxt[y0:y1])
            live += band_live
            changed += band_changed

    def step(self) -> StepStats:
        slots, bands = self._layout()
        a, nxt = self.bits, self._next
        todo = deque(bands)
        if len(slots) == 1:
            population, changed = self._work(a, nxt, todo, slots[0])
        else:
            pool = _executor()
            futures = []
            for slot in slots[1:]:
                try:
                    futures.append(pool.submit(self._work, a, nxt, todo, slot))
                except RuntimeError:                # set_threads() on another thread closed this
                    break                           # pool: the bands left are worked below
            try:
                population, changed = self._work(a, nxt, todo, slots[0])
            finally:
                wait(futures)                       # no band may still be writing after this
            for future in futures:
                live, flipped = future.result()
                population += live
                changed += flipped
        born = (changed + population - self.population) // 2
        self.bits, self._next = nxt, a
        self.generation += 1
        self.population = population
        self.version += 1
        return StepStats(self.generation, population, born, changed - born)
