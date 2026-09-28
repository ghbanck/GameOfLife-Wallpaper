"""The pattern library in the control panel: icons, search, and a note on hover.

Runs on the panel's Tk thread only.  With close to 5000 patterns nothing is
done for a row until it is actually on screen: the list is filled with names,
and each row's icon is drawn the first time the row scrolls into view.  The
hover card animates the pattern -- an oscillator through its period, a
spaceship flying on the spot, a methuselah or a gun through its first
generations -- because a still picture of a gun says very little about it.
"""

from __future__ import annotations

import base64
import math
from collections import OrderedDict

import numpy as np

from ..core import library, patterns
from . import theme
from .icon import png_bytes
from .text import category, language, num, t

SEARCH_LIMIT = 400
ICON_CACHE = 900
TIP_DELAY_MS = 380
FRAME_MS = 110
ANIMATE_MAX_SIDE = 120
ANIMATE_MAX_POP = 6000


# ================================================================== drawing ===
def _rgb(text: str) -> tuple[int, int, int]:
    return tuple(int(text[i:i + 2], 16) for i in (1, 3, 5))


def render_cells(cells: np.ndarray | None, size: int, colour: tuple[int, int, int],
                 tile: tuple[int, int, int] | None = None, preview: np.ndarray | None = None,
                 radius: int = 3, pad: int = 2) -> np.ndarray:
    """A pattern drawn into a size x size RGBA square.

    Small patterns keep their cells as separate squares; big ones are shrunk
    to a brightness map (from the library's ready-made preview when there is
    one, so a 7000-cell-wide construction is never decoded just for an icon).
    """
    img = np.zeros((size, size, 4), np.uint8)
    if tile is not None:
        yy, xx = np.mgrid[0:size, 0:size]
        r = radius
        inside = np.ones((size, size), bool)
        for cy, cx in ((r, r), (r, size - 1 - r), (size - 1 - r, r), (size - 1 - r, size - 1 - r)):
            corner = ((yy < r) if cy == r else (yy > size - 1 - r)) & ((xx < r) if cx == r else (xx > size - 1 - r))
            inside &= ~(corner & ((yy - cy) ** 2 + (xx - cx) ** 2 > r * r))
        img[inside, :3] = tile
        img[inside, 3] = 255
    area = size - 2 * pad
    if area <= 0:
        return img
    level = None
    if cells is not None and cells.size:
        h, w = cells.shape
        side = max(h, w)
        if side * 2 <= area:
            cell = area // side
            gap = 1 if cell >= 4 else 0
            level = np.zeros((h * cell, w * cell), np.float32)
            block = np.zeros((cell, cell), np.float32)
            block[:cell - gap, :cell - gap] = 1.0
            level = np.kron(cells.astype(np.float32), block)
        else:
            factor = math.ceil(side / area)
            ph, pw = -(-h // factor) * factor, -(-w // factor) * factor
            padded = np.zeros((ph, pw), np.float32)
            padded[:h, :w] = cells
            blocks = padded.reshape(ph // factor, factor, pw // factor, factor)
            if factor <= 3:
                level = blocks.max(axis=(1, 3))
            else:
                level = np.sqrt(np.minimum(1.0, blocks.mean(axis=(1, 3)) * 3.0))
    elif preview is not None:
        src = preview.astype(np.float32) / 255.0
        idx = (np.arange(area) * src.shape[0] // area).clip(0, src.shape[0] - 1)
        level = src[np.ix_(idx, idx)]
    if level is None:
        return img
    lh, lw = level.shape
    oy, ox = pad + (area - lh) // 2, pad + (area - lw) // 2
    region = img[oy:oy + lh, ox:ox + lw]
    alpha = level[..., None]
    base = region[..., :3].astype(np.float32)
    region[..., :3] = (np.array(colour, np.float32) * alpha + base * (1 - alpha)).astype(np.uint8)
    region[..., 3] = np.maximum(region[..., 3], (level * 255).astype(np.uint8))
    return img


def photo(tk, master, rgba: np.ndarray):
    return tk.PhotoImage(master=master, data=base64.b64encode(png_bytes(rgba)).decode("ascii"))


# ================================================================ simulating ===
def _crop(c: np.ndarray):
    rows = np.flatnonzero(c.any(axis=1))
    if not rows.size:
        return c[:0, :0], 0, 0
    cols = np.flatnonzero(c.any(axis=0))
    return c[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1], int(cols[0]), int(rows[0])


def _step(c: np.ndarray) -> np.ndarray:
    a = np.pad(c, 2)
    n = (a[:-2, :-2] + a[:-2, 1:-1] + a[:-2, 2:] + a[1:-1, :-2] + a[1:-1, 2:]
         + a[2:, :-2] + a[2:, 1:-1] + a[2:, 2:])
    return ((n == 3) | ((n == 2) & (a[1:-1, 1:-1] == 1))).astype(np.uint8)


def animation(entry, cells: np.ndarray) -> list[np.ndarray]:
    """The frames the hover card cycles through, all the same size (maybe just one)."""
    if (entry.category == "still" or entry.period == 1 or max(cells.shape) > ANIMATE_MAX_SIDE
            or int(cells.sum()) > ANIMATE_MAX_POP):
        return [cells]
    count = entry.period if entry.period else 90
    count = max(1, min(count, 90))
    frames, c, ox, oy = [], cells, 0, 0
    for _ in range(count):
        frames.append((c, ox, oy))
        c, dx, dy = _crop(_step(c))
        ox += dx - 1
        oy += dy - 1
        if not c.size:
            frames.append((c, ox, oy))
            break
    if entry.moves:
        # Fly on the spot: centre every frame in the largest one.
        h = max(f[0].shape[0] for f in frames)
        w = max(f[0].shape[1] for f in frames)
        out = []
        for f, _, _ in frames:
            canvas = np.zeros((h, w), np.uint8)
            y0, x0 = (h - f.shape[0]) // 2, (w - f.shape[1]) // 2
            canvas[y0:y0 + f.shape[0], x0:x0 + f.shape[1]] = f
            out.append(canvas)
        return out
    # A fixed window: the pattern's own box grown a little, so debris and
    # escaping gliders show leaving rather than stretching the picture.
    h0, w0 = cells.shape
    grow = max(6, max(h0, w0) // 2)
    left, top = -grow, -grow
    width, height = w0 + 2 * grow, h0 + 2 * grow
    if entry.period:
        xs = [ox for f, ox, _ in frames if f.size] + [ox + f.shape[1] for f, ox, _ in frames if f.size]
        ys = [oy for f, _, oy in frames if f.size] + [oy + f.shape[0] for f, _, oy in frames if f.size]
        left, top = min(xs), min(ys)
        width, height = max(xs) - left, max(ys) - top
    out = []
    for f, ox, oy in frames:
        canvas = np.zeros((height, width), np.uint8)
        if f.size:
            x0, y0 = ox - left, oy - top
            sx0, sy0 = max(0, -x0), max(0, -y0)
            dx0, dy0 = max(0, x0), max(0, y0)
            w_ = min(f.shape[1] - sx0, width - dx0)
            h_ = min(f.shape[0] - sy0, height - dy0)
            if w_ > 0 and h_ > 0:
                canvas[dy0:dy0 + h_, dx0:dx0 + w_] = f[sy0:sy0 + h_, sx0:sx0 + w_]
        out.append(canvas)
    return out


# ================================================================ describing ===
CLASSIC_KINDS = {patterns.STILL: "still", patterns.OSC: "osc", patterns.SHIP: "ship",
                 patterns.METHUSELAH: "methuselah", patterns.GUN: "gun"}


def kind_line(entry) -> str:
    cat = entry.category
    if cat == "classic":
        info = patterns.BY_KEY.get(entry.key)
        cat = CLASSIC_KINDS.get(info.category, "other") if info is not None else "other"
        if entry.key == "infinite_growth":
            cat = "puffer"                          # "Puffer / growth": it grows, it fires nothing
    parts = [t(f"lib.kind.{cat}")]
    if entry.moves:
        speed, direction = library.speed_text(entry)
        parts.append(f"{speed} {t('lib.dir.' + direction)}")
    if entry.period > 1:
        parts.append(t("lib.period", p=entry.period))
    if entry.kind == "dies" and entry.lifespan:
        parts.append(t("lib.dies", n=num(entry.lifespan)))
    elif entry.kind == "settles" and entry.lifespan:
        parts.append(t("lib.settles", n=num(entry.lifespan)))
    return "  ·  ".join(parts)


def size_line(entry) -> str:
    return t("lib.size", w=num(entry.width), h=num(entry.height), n=num(entry.population))


def credit_line(entry) -> str:
    who = entry.by.strip()
    if who and entry.year:
        return t("lib.found", who=who, year=entry.year)
    if who:
        return t("lib.found.who", who=who)
    if entry.year:
        return t("lib.found.year", year=entry.year)
    return ""


def note_text(entry) -> str:
    note = entry.note_pt if language() == "pt" else entry.note_en
    if note:
        return note
    cat = entry.category
    if entry.kind == "dies" and entry.lifespan:
        return t("lib.about.dies", n=num(entry.lifespan))
    if cat == "osc" and entry.period:
        return t("lib.about.osc", p=entry.period)
    if cat == "ship" and entry.period:
        a = max(abs(entry.dx), abs(entry.dy))
        return t("lib.about.ship", n=a, p=entry.period)
    if cat == "methuselah" and entry.lifespan:
        return t("lib.about.methuselah.n", n=num(entry.lifespan))
    return t(f"lib.about.{cat}")


def sources_line(entry) -> str:
    names = [t(f"lib.source.{s}") for s in entry.sources if s != "golwall"]
    return t("lib.sources", names=" · ".join(names)) if names else ""


def short_info(entry) -> str:
    """The small right-hand column of a row."""
    if entry.moves:
        return library.speed_text(entry)[0]
    if entry.period > 1:
        return f"p{entry.period}"
    if entry.kind == "dies" and entry.lifespan:
        return f"†{entry.lifespan}"
    if entry.kind == "settles" and entry.lifespan >= 100:
        return f"~{entry.lifespan}"
    return f"{entry.width}×{entry.height}" if max(entry.width, entry.height) >= 100 else ""


# ================================================================ the widgets ===
class LibraryBrowser:
    """Search, category, the list itself, and the buttons under it."""

    def __init__(self, panel, parent) -> None:
        self.panel = panel
        tk, ttk, px = panel.tk, panel.ttk, panel.px
        self.tk, self.ttk, self.px = tk, ttk, px
        self.root = panel.root
        self.app = panel.app
        self.lib = library.get()
        self.icon_size = px(26)
        self.icons: OrderedDict[str, object] = OrderedDict()
        self._iconed: set[str] = set()
        self._listed: list = []
        self._pending_icons = None
        self._search_job = None
        self._current = None
        self._pattern_note = ""
        self._refilling = False

        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x")
        self.query = tk.StringVar(master=self.root)
        self.search = ttk.Entry(row, textvariable=self.query)
        self.search.pack(side="left", fill="x", expand=True)
        self.search.bind("<KeyRelease>", self._on_query)
        self.search.bind("<Escape>", self._escape)
        self.search.bind("<Down>", lambda _e: self._focus_list())
        self.placeholder = ttk.Label(row, text=t("lib.search"), style="Placeholder.TLabel")
        self.placeholder.place(in_=self.search, x=px(8), rely=0.5, anchor="w")
        self.placeholder.bind("<Button-1>", lambda _e: self.search.focus_set())
        ttk.Button(row, text="×", width=2, style="Small.TButton",
                   command=self._clear_query).pack(side="left", padx=(px(4), 0))

        self.category = ttk.Combobox(parent, state="readonly")
        self.category.pack(fill="x", pady=(px(6), 0))
        self.category.bind("<<ComboboxSelected>>", lambda _e: self._on_category())

        holder = ttk.Frame(parent, style="Card.TFrame")
        holder.pack(fill="both", expand=True, pady=(px(6), 0))
        self.tree = ttk.Treeview(holder, show="tree", columns=("info",), selectmode="browse",
                                 style="Library.Treeview", height=7)
        self.tree.column("#0", width=px(262), stretch=True)
        self.tree.column("info", width=px(64), anchor="e", stretch=False)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self._yview)
        self.scroll = scroll
        self.tree.configure(yscrollcommand=self._on_scroll)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Motion>", self._on_motion)
        self.tree.bind("<Leave>", lambda _e: self.tip.cancel())
        self.tree.bind("<Configure>", lambda _e: self._queue_icons())
        self.tree.bind("<MouseWheel>", lambda _e: self.tip.cancel(), add="+")
        self.tree.bind("<ButtonPress>", lambda _e: self.tip.cancel(), add="+")
        self.tree.bind("<Double-1>", lambda _e: self._stamp_tool())
        self.tree.bind("<Return>", lambda _e: self._stamp_tool())

        self.note = ttk.Label(parent, text="", style="CardMuted.TLabel", wraplength=px(320), justify="left")
        self.note.pack(anchor="w", pady=(px(4), 0))
        self.tip = PatternTip(self)
        self._fill_categories()

    # -- categories and search --------------------------------------------------
    def _fill_categories(self, keep: str | None = None) -> None:
        self.groups = self.lib.categories()
        self._cat_keys = [c for c, _ in self.groups]
        labels = [f"{category(c)}  ({num(len(items))})" for c, items in self.groups]
        self.category.configure(values=labels)
        index = self._cat_keys.index(keep) if keep in self._cat_keys else (
            self._cat_keys.index("ship") if "ship" in self._cat_keys else 0)
        self.category.current(index)
        self.fill()

    def refresh_user(self) -> int:
        current = self._cat_keys[max(0, self.category.current())] if self._cat_keys else None
        count = self.lib.refresh_user()
        self._fill_categories(keep=current)
        return count

    def show_category(self, cat: str) -> None:
        if cat in self._cat_keys:
            self._clear_query(fill=False)
            self.category.current(self._cat_keys.index(cat))
            self.fill()

    def _on_category(self) -> None:
        self._clear_query(fill=False)
        self.fill()

    def _on_query(self, event=None) -> None:
        if event is not None and event.keysym in ("Down", "Up", "Escape", "Return"):
            return
        self._update_placeholder()
        if self._search_job is not None:
            self.root.after_cancel(self._search_job)
        self._search_job = self.root.after(160, self.fill)

    def _escape(self, _event=None):
        """Esc in the search box empties it; only an empty box lets Esc close the editor."""
        if self.query.get():
            self._clear_query()
            return "break"
        return None

    def _clear_query(self, _event=None, fill: bool = True) -> None:
        if self.query.get():
            self.query.set("")
            if fill:
                self.fill()
        self._update_placeholder()

    def _update_placeholder(self) -> None:
        if self.query.get():
            self.placeholder.place_forget()
        else:
            self.placeholder.place(in_=self.search, x=self.px(8), rely=0.5, anchor="w")

    def _focus_list(self) -> None:
        if self._search_job is not None:            # typed within the debounce: search first
            self.root.after_cancel(self._search_job)
            self.fill()
            self._refilling = False
        children = self.tree.get_children()
        if children:
            self.tree.focus_set()
            first = self.tree.selection()[0] if self.tree.selection() else children[0]
            self.tree.focus(first)
            self.tree.selection_set(first)

    def fill(self) -> None:
        self._search_job = None
        text = self.query.get().strip()
        if text:
            found = self.lib.search(text)
            items = found[:SEARCH_LIMIT]
            if not found:
                self.note.configure(text=t("lib.none"))
            elif len(found) > SEARCH_LIMIT:
                self.note.configure(text=t("lib.more", n=num(SEARCH_LIMIT), total=num(len(found))))
            else:
                self.note.configure(text=t("lib.found.n", n=num(len(found))))
        else:
            index = max(0, self.category.current())
            items = self.groups[index][1] if index < len(self.groups) else []
            if self._cat_keys and self._cat_keys[index] == library.USER_CATEGORY and not items:
                self.note.configure(text=t("lib.mine.empty"))
            else:
                self.note.configure(text=self._pattern_note if self._current else "")
        tree = self.tree
        # Selecting the current pattern again below fires <<TreeviewSelect>>
        # like a click would; that must not count as the user picking it.
        self._refilling = True
        tree.delete(*tree.get_children())
        self._iconed.clear()
        self._listed = items
        for entry in items:
            tree.insert("", "end", iid=entry.key, text="  " + entry.name, values=(short_info(entry),))
        if self._current in {e.key for e in items}:
            tree.selection_set(self._current)
            tree.see(self._current)
        else:
            tree.yview_moveto(0.0)
        self.root.after_idle(self._refilled)        # idle runs after the queued select events
        self._queue_icons()

    def _refilled(self) -> None:
        self._refilling = False

    # -- lazy icons -------------------------------------------------------------------
    def _yview(self, *args) -> None:
        self.tree.yview(*args)
        self.tip.cancel()
        self._queue_icons()

    def _on_scroll(self, first, last) -> None:
        self.scroll.set(first, last)
        self._queue_icons()

    def _queue_icons(self) -> None:
        if self._pending_icons is None:
            self._pending_icons = self.root.after(15, self._draw_visible_icons)

    def _visible_keys(self) -> list[str]:
        tree = self.tree
        height = tree.winfo_height()
        step = max(4, self.px(30) // 2)
        keys, seen = [], set()
        for y in range(1, max(2, height), step):
            iid = tree.identify_row(y)
            if iid and iid not in seen:
                seen.add(iid)
                keys.append(iid)
        return keys

    def _draw_visible_icons(self) -> None:
        self._pending_icons = None
        try:
            keys = self._visible_keys()
        except self.tk.TclError:
            return
        for key in keys:
            if key in self._iconed:
                continue
            image = self.icon_for(key)
            if image is not None:
                try:
                    self.tree.item(key, image=image)
                except self.tk.TclError:
                    continue
                self._iconed.add(key)

    def icon_for(self, key: str):
        image = self.icons.get(key)
        if image is not None:
            self.icons.move_to_end(key)
            return image
        entry = self.lib.get(key)
        if entry is None:
            return None
        preview = self.lib.preview(key)
        cells = None
        if preview is None:
            try:
                cells = self.lib.cells(key)
            except Exception:
                return None
        rgba = render_cells(cells, self.icon_size, self.panel.accent, tile=_rgb(theme.BG), preview=preview,
                            radius=self.px(4), pad=self.px(3))
        image = photo(self.tk, self.root, rgba)
        self.icons[key] = image
        while len(self.icons) > ICON_CACHE:
            old, _ = self.icons.popitem(last=False)
            if old in self._iconed:
                try:
                    self.tree.item(old, image="")
                except self.tk.TclError:
                    pass
                self._iconed.discard(old)
        return image

    def on_accent(self) -> None:
        """The palette changed: redraw the icons in the new accent colour."""
        self.icons.clear()
        for key in list(self._iconed):
            try:
                self.tree.item(key, image="")
            except self.tk.TclError:
                pass
        self._iconed.clear()
        self.tip.flush()
        self._queue_icons()

    # -- choosing ---------------------------------------------------------------------
    def _on_select(self, _event=None) -> None:
        if self._refilling:
            return
        picked = self.tree.selection()
        if not picked:
            return
        key = picked[0]
        entry = self.lib.get(key)
        if entry is None:
            return
        self._current = key
        self.app.call(self.app.editor_call, "set_pattern", key)
        text = f"{kind_line(entry)}\n{size_line(entry)}"
        world = self.app.snapshot().get("world_size")
        if world and (entry.width > world[0] or entry.height > world[1]):
            text += "\n" + t("lib.too_big")
        self._pattern_note = text
        self.note.configure(text=text)

    def _stamp_tool(self) -> None:
        self.panel._set_tool("pattern")

    def pick_index(self, index: int) -> None:
        children = self.tree.get_children()
        if index < len(children):
            self.tree.selection_set(children[index])
            self.tree.see(children[index])
            self.tree.focus(children[index])

    def selected(self):
        picked = self.tree.selection()
        return self.lib.get(picked[0]) if picked else None

    def fit_rows(self, rows: int) -> None:
        self.tree.configure(height=rows)

    @property
    def rows(self) -> int:
        return int(self.tree.cget("height"))

    # -- hover ------------------------------------------------------------------------
    def _on_motion(self, event) -> None:
        key = self.tree.identify_row(event.y)
        if not key:
            self.tip.cancel()
            return
        self.tip.hover(key, event.x_root, event.y_root)


class PatternTip:
    """The card shown while the pointer rests on a row."""

    def __init__(self, browser: LibraryBrowser) -> None:
        self.browser = browser
        tk, px = browser.tk, browser.px
        self.tk, self.px = tk, px
        root = browser.root
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        try:
            self.win.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.win.configure(background=theme.BORDER)
        card = tk.Frame(self.win, background=theme.SURFACE, padx=px(10), pady=px(8))
        card.pack(padx=1, pady=1)
        self.size = px(150)
        self.picture = tk.Label(card, background=theme.BG, borderwidth=0)
        self.picture.grid(row=0, column=0, rowspan=5, sticky="n", padx=(0, px(10)))
        width = px(250)
        self.title = tk.Label(card, background=theme.SURFACE, foreground=theme.TEXT, anchor="w",
                              justify="left", font=("Segoe UI Semibold", 10), wraplength=width)
        self.title.grid(row=0, column=1, sticky="w")
        self.kind = tk.Label(card, background=theme.SURFACE, foreground=theme.TEXT, anchor="w",
                             justify="left", font=("Segoe UI", 9), wraplength=width)
        self.kind.grid(row=1, column=1, sticky="w")
        self.facts = tk.Label(card, background=theme.SURFACE, foreground=theme.MUTED, anchor="w",
                              justify="left", font=("Segoe UI", 8), wraplength=width)
        self.facts.grid(row=2, column=1, sticky="w")
        self.note = tk.Label(card, background=theme.SURFACE, foreground=theme.TEXT, anchor="w",
                             justify="left", font=("Segoe UI", 9), wraplength=width)
        self.note.grid(row=3, column=1, sticky="w", pady=(px(6), 0))
        self.source = tk.Label(card, background=theme.SURFACE, foreground=theme.DIM, anchor="w",
                               justify="left", font=("Segoe UI", 8), wraplength=width)
        self.source.grid(row=4, column=1, sticky="sw", pady=(px(6), 0))
        self._key = None
        self._shown_key = None
        self._at = (0, 0)
        self._job = None
        self._anim_job = None
        self._frames: list[np.ndarray] = []
        self._images: dict[int, object] = {}
        self._frame = 0
        self._cache: OrderedDict[str, list[np.ndarray]] = OrderedDict()

    def hover(self, key: str, x: int, y: int) -> None:
        self._at = (x, y)
        if key == self._shown_key:
            if self._job is not None:               # back on the shown row: forget the pending one
                self.browser.root.after_cancel(self._job)
                self._job = None
            self._key = key
            return
        if key != self._key:
            self._key = key
            if self._job is not None:
                self.browser.root.after_cancel(self._job)
            delay = 60 if self._shown_key else TIP_DELAY_MS       # moving along the list: follow quickly
            self._job = self.browser.root.after(delay, self._show)

    def cancel(self) -> None:
        if self._job is not None:
            self.browser.root.after_cancel(self._job)
            self._job = None
        self._key = None
        self.hide()

    def hide(self) -> None:
        if self._anim_job is not None:
            self.browser.root.after_cancel(self._anim_job)
            self._anim_job = None
        self._shown_key = None
        try:
            self.win.withdraw()
        except self.tk.TclError:
            pass

    def flush(self) -> None:
        """The accent changed: draw the frames again in the new colour."""
        self._images.clear()
        if self._shown_key is not None:
            self._paint()

    def _show(self) -> None:
        self._job = None
        key = self._key
        lib = self.browser.lib
        entry = lib.get(key) if key else None
        if entry is None:
            return
        self._shown_key = key
        self.title.configure(text=entry.name)
        self.kind.configure(text=kind_line(entry))
        facts = size_line(entry)
        credit = credit_line(entry)
        if credit:
            facts += "\n" + credit
        self.facts.configure(text=facts)
        self.note.configure(text=note_text(entry))
        self.source.configure(text=sources_line(entry))
        self._frames = self._frames_for(entry)
        self._images = {}
        self._frame = 0
        self._paint()
        self._place()
        self.win.deiconify()
        self.win.lift()
        if len(self._frames) > 1:
            self._anim_job = self.browser.root.after(FRAME_MS, self._tick)

    def _frames_for(self, entry) -> list[np.ndarray]:
        frames = self._cache.get(entry.key)
        if frames is not None:
            return frames
        lib = self.browser.lib
        preview = lib.preview(entry.key)
        if preview is not None:
            frames = [("preview", preview)]
        else:
            try:
                cells = lib.cells(entry.key)
                frames = [("cells", f) for f in animation(entry, cells)]
            except Exception:
                frames = []
        self._cache[entry.key] = frames
        while len(self._cache) > 12:
            self._cache.popitem(last=False)
        return frames

    def _paint(self) -> None:
        if not self._frames:
            self.picture.configure(image="")
            return
        index = self._frame % len(self._frames)
        image = self._images.get(index)
        if image is None:
            kind, data = self._frames[index]
            colour = self.browser.panel.accent
            rgba = render_cells(data if kind == "cells" else None, self.size, colour, tile=_rgb(theme.BG),
                                preview=data if kind == "preview" else None, radius=self.px(6), pad=self.px(8))
            image = photo(self.tk, self.browser.root, rgba)
            self._images[index] = image
        self.picture.configure(image=image)

    def _tick(self) -> None:
        self._anim_job = None
        if self._shown_key is None:
            return
        self._frame += 1
        self._paint()
        self._anim_job = self.browser.root.after(FRAME_MS, self._tick)

    def _place(self) -> None:
        win = self.win
        win.update_idletasks()
        width, height = win.winfo_reqwidth(), win.winfo_reqheight()
        x, y = self._at
        root = self.browser.root
        left, top = root.winfo_vrootx(), root.winfo_vrooty()
        right = left + root.winfo_screenwidth()
        bottom = top + root.winfo_screenheight()
        try:
            from ..native import win32 as w
            left, top, right, bottom = w.work_area((x, y))
        except Exception:
            pass
        # Beside the panel rather than over the list the pointer is reading.
        panel_x = root.winfo_rootx()
        gx = panel_x - width - self.px(8)
        if gx < left:
            gx = root.winfo_rootx() + root.winfo_width() + self.px(8)
        if gx + width > right:
            gx = max(left, x - width - self.px(20))
        gy = max(top, min(y - height // 3, bottom - height))
        win.geometry(f"+{int(gx)}+{int(gy)}")
