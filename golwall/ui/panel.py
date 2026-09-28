"""The control panel: real widgets, on a thread of their own.

Tk must never share a thread with another message loop.  Tk's window
procedure services its event queue before returning, so when a foreign loop
dispatches a Tk window's message, Tk runs Python callbacks from a place where
``_tkinter`` has no thread state to restore -- and the interpreter aborts with
"the GIL is released, the current Python thread state is NULL".  That was the
crash that used to take the whole wallpaper down.

So the panel lives on its own thread with Tk's own ``mainloop``.  It reads the
app's state through ``app.snapshot()`` and changes it only through
``app.call(...)``, which queues the work for the main thread.  The main thread
talks to the panel only through ``PanelHost``, which queues messages the other
way.  No object crosses between the two.
"""

from __future__ import annotations

import base64
import gc
import math
import queue
import sys
import threading

from ..capabilities import worlds
from ..core import palettes
from ..native import win32 as w
from . import theme
from .editor import TOOL_BRUSH, TOOL_PATTERN, TOOL_SELECT
from .library_view import LibraryBrowser
from .text import RULES, language, num, t

SPEED_MIN, SPEED_MAX = 0.5, 60.0
PATTERN_FILES = (("Life patterns", "*.rle *.cells *.lif *.life *.txt"), ("All files", "*.*"))


def _slider_to_speed(value: float) -> float:
    return SPEED_MIN * (SPEED_MAX / SPEED_MIN) ** (value / 100.0)


def _speed_to_slider(speed: float) -> float:
    speed = min(SPEED_MAX, max(SPEED_MIN, speed))
    return 100.0 * math.log(speed / SPEED_MIN) / math.log(SPEED_MAX / SPEED_MIN)


class PanelHost:
    """The main thread's handle on the panel thread."""

    def __init__(self, app) -> None:
        self.app = app
        self._inbox: queue.SimpleQueue = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.hwnd = 0
        self.wanted = False

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _ensure(self) -> None:
        with self._lock:
            if self.alive:
                return
            self._inbox = queue.SimpleQueue()
            self._thread = threading.Thread(target=self._main, name="golwall-panel", daemon=True)
            self._thread.start()

    def show(self) -> None:
        self.wanted = True
        self._ensure()
        self._inbox.put(("show",))

    def hide(self) -> None:
        self.wanted = False
        if self.alive:
            self._inbox.put(("hide",))

    def post(self, *message) -> None:
        if self.alive:
            self._inbox.put(message)

    def shutdown(self, timeout: float = 3.0) -> bool:
        """Close the panel thread; False if it is still running (a dialog was open)."""
        thread = self._thread
        if thread is not None and thread.is_alive():
            self._inbox.put(("quit",))
            thread.join(timeout)
        if thread is None or not thread.is_alive():
            self._thread = None
            return True
        return False

    def _main(self) -> None:
        panel = None
        try:
            panel = ControlPanel(self, self._inbox)
            panel.run()
        except Exception:
            # Drawing still works without the panel (hotkey and tray toggle it),
            # so a broken Tk only costs the widgets.
            self.app.log_exception("control panel")
        finally:
            if panel is not None:
                panel.dispose()
            panel = None
            # A traceback kept in sys.last_* holds widgets, and through them the
            # Tcl interpreter -- which must be deleted on this thread, not at exit.
            for name in ("last_type", "last_value", "last_traceback", "last_exc"):
                if hasattr(sys, name):
                    setattr(sys, name, None)
            gc.collect()                      # Tk objects must die on this thread
            self.hwnd = 0


class ControlPanel:
    def __init__(self, host: PanelHost, inbox: queue.SimpleQueue) -> None:
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.host, self.app, self.inbox = host, host.app, inbox
        self.root = tk.Tk()
        try:
            self._setup(host)
        except BaseException:
            # A half-built root left behind would stay tkinter's default root,
            # pointing at this thread's interpreter after the thread is gone.
            try:
                self.root.destroy()
            except Exception:
                pass
            raise

    def _report_callback_error(self, kind, value, tb) -> None:
        """Tk's default handler parks the traceback in sys.last_*; log it instead."""
        self.app.log.error("control panel callback failed", exc_info=(kind, value, tb))

    def _setup(self, host: PanelHost) -> None:
        tk = self.tk
        self.root.report_callback_exception = self._report_callback_error
        self.root.withdraw()
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        self.accent = self.app.accent_rgb()
        theme.apply(self.root, self.scale, self.accent)
        self.root.title(t("panel.title"))
        self.root.resizable(False, False)
        try:
            from .icon import glider_rgba, png_bytes
            self._icon = tk.PhotoImage(master=self.root,
                                       data=base64.b64encode(png_bytes(glider_rgba(32))).decode())
            self.root.iconphoto(True, self._icon)
        except tk.TclError:
            self._icon = None
        self.root.protocol("WM_DELETE_WINDOW", self.on_done)
        self._syncing = False
        self._speed_drag = False
        self._trail_drag = False
        self._density_drag = False
        self._shown = False
        self._message_serial = -1
        self._alive = True
        self._build()
        self._bind_keys()
        self.root.update_idletasks()
        try:
            self.frame_hwnd = int(self.root.wm_frame(), 16)
        except (tk.TclError, ValueError):
            self.frame_hwnd = 0
        if self.frame_hwnd:
            w.set_dark_title_bar(self.frame_hwnd)
            host.hwnd = self.frame_hwnd
        self.refresh(force=True)
        self.root.after(30, self._poll)
        self.root.after(250, self._tick)

    def px(self, value: float) -> int:
        return max(1, int(round(value * self.scale)))

    # -- layout -------------------------------------------------------------
    def _card(self, parent, **pack):
        frame = self.ttk.Frame(parent, style="Card.TFrame", padding=(self.px(10), self.px(8)))
        frame.pack(fill="x", **pack)
        return frame

    def _section(self, parent, text: str) -> None:
        self.ttk.Label(parent, text=text.upper(), style="Section.TLabel").pack(
            anchor="w", pady=(self.px(6), self.px(3)))

    def _row(self, parent, **pack):
        row = self.ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x", **pack)
        return row

    def _build(self) -> None:
        tk, ttk, px, app = self.tk, self.ttk, self.px, self.app
        outer = ttk.Frame(self.root, padding=(px(12), px(10), px(12), px(10)))
        outer.pack(fill="both", expand=True)

        # -- header ---------------------------------------------------------
        head = ttk.Frame(outer)
        head.pack(fill="x")
        ttk.Label(head, text=t("app"), style="Title.TLabel").pack(side="left")
        self.status = ttk.Label(outer, text="", style="Stat.TLabel")
        self.status.pack(anchor="w", pady=(px(2), 0))
        self.mode_label = ttk.Label(outer, text=t("panel.mode"), style="Accent.TLabel",
                                    wraplength=px(340), justify="left")
        self.mode_label.pack(anchor="w", pady=(px(2), px(8)))

        # -- transport --------------------------------------------------------
        card = self._card(outer, pady=(0, px(8)))
        row = self._row(card)
        self.play_button = ttk.Button(row, text=t("panel.pause"), style="Accent.TButton",
                                      width=9, command=lambda: app.call(app.toggle_pause))
        self.play_button.pack(side="left")
        ttk.Button(row, text=t("panel.step"), width=7,
                   command=lambda: app.call(app.step_once)).pack(side="left", padx=(px(6), 0))
        self.undo_button = ttk.Button(row, text=t("panel.undo"), width=9,
                                      command=lambda: app.call(app.editor_call, "undo"))
        self.undo_button.pack(side="left", padx=(px(6), 0))
        row = self._row(card, pady=(px(8), 0))
        ttk.Label(row, text=t("panel.speed"), style="Card.TLabel").pack(side="left")
        self.speed_value = ttk.Label(row, text="", style="CardMuted.TLabel", width=11, anchor="e")
        self.speed_value.pack(side="right")
        self.speed = ttk.Scale(row, from_=0, to=100, command=self.on_speed)
        self.speed.pack(side="left", fill="x", expand=True, padx=px(8))
        self.speed.bind("<ButtonPress-1>", lambda _e: setattr(self, "_speed_drag", True))
        self.speed.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_speed_drag", False))

        # -- tabs -----------------------------------------------------------------
        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        self.notebook = notebook
        self._build_draw(notebook)
        self._build_world(notebook)
        self._build_look(notebook)

        # -- footer ---------------------------------------------------------------
        self.message = ttk.Label(outer, text="", style="Muted.TLabel", wraplength=px(340), justify="left")
        self.message.pack(anchor="w", pady=(px(8), 0))
        ttk.Label(outer, text=t("panel.hint"), style="Muted.TLabel", wraplength=px(340),
                  justify="left").pack(anchor="w", pady=(px(4), 0))
        foot = ttk.Frame(outer)
        foot.pack(fill="x", pady=(px(8), 0))
        ttk.Button(foot, text=t("panel.close"), style="Accent.TButton",
                   command=self.on_done).pack(side="right")

    def _build_draw(self, notebook) -> None:
        tk, ttk, px, app = self.tk, self.ttk, self.px, self.app
        tab = ttk.Frame(notebook, style="Card.TFrame", padding=(px(10), px(4), px(10), px(10)))
        notebook.add(tab, text=t("panel.tab.draw"))

        self._section(tab, t("panel.tool"))
        row = self._row(tab)
        self.tool = tk.StringVar(master=self.root, value=TOOL_PATTERN)
        for label, value in ((t("panel.tool.pattern"), TOOL_PATTERN), (t("panel.tool.brush"), TOOL_BRUSH),
                             (t("panel.tool.select"), TOOL_SELECT)):
            ttk.Radiobutton(row, text=label, value=value, variable=self.tool, style="Toggle.TRadiobutton",
                            command=lambda: app.call(app.editor_call, "set_tool", self.tool.get())
                            ).pack(side="left", fill="x", expand=True, padx=(0, px(4)))
        row = self._row(tab, pady=(px(8), 0))
        ttk.Label(row, text=t("panel.brush"), style="Card.TLabel").pack(side="left")
        self.brush = tk.IntVar(master=self.root, value=1)
        spin = ttk.Spinbox(row, from_=1, to=64, width=4, textvariable=self.brush, command=self.on_brush)
        spin.pack(side="left", padx=px(6))
        spin.bind("<Return>", lambda _e: self.on_brush())
        spin.bind("<FocusOut>", lambda _e: self.on_brush())
        self.brush_spin = spin
        ttk.Button(row, text=t("panel.flip"), style="Small.TButton",
                   command=lambda: app.call(app.editor_call, "flip_current", "h")).pack(side="right")
        ttk.Button(row, text=t("panel.rotate"), style="Small.TButton",
                   command=lambda: app.call(app.editor_call, "rotate_by", 1)).pack(side="right", padx=px(4))

        self._section(tab, t("panel.library"))
        self.library = LibraryBrowser(self, tab)
        row = self._row(tab, pady=(px(6), 0))
        ttk.Button(row, text=t("panel.import"), style="Small.TButton",
                   command=self.on_import).pack(side="left")
        ttk.Button(row, text=t("panel.paste_rle"), style="Small.TButton",
                   command=lambda: app.call(app.editor_call, "paste")).pack(side="left", padx=px(4))
        ttk.Button(row, text=t("lib.open_world"), style="Small.TButton",
                   command=self.on_open_as_world).pack(side="left")
        row = self._row(tab, pady=(px(4), 0))
        ttk.Button(row, text=t("lib.folder"), style="Small.TButton",
                   command=lambda: app.call(app.open_patterns_folder)).pack(side="left")
        ttk.Button(row, text=t("lib.refresh"), style="Small.TButton",
                   command=self.on_refresh_library).pack(side="left", padx=px(4))

        self._section(tab, t("panel.selection"))
        grid = ttk.Frame(tab, style="Card.TFrame")
        grid.pack(fill="x")
        buttons = (("panel.sel.copy", "copy_selection", ()), ("panel.sel.cut", "cut_selection", ()),
                   ("panel.sel.paste", "paste", ()), ("panel.sel.delete", "delete_selection", ()),
                   ("panel.sel.fill", "fill_selection", (0.3,)),
                   ("panel.sel.rotate", "transform_selection", ("rotate",)),
                   ("panel.sel.fliph", "transform_selection", ("flip_h",)),
                   ("panel.sel.flipv", "transform_selection", ("flip_v",)),
                   ("panel.sel.visible", "select_all_visible", ()),
                   ("panel.sel.none", "clear_selection", ()))
        for i, (label, method, args) in enumerate(buttons):
            ttk.Button(grid, text=t(label), style="Small.TButton",
                       command=lambda m=method, a=args: app.call(app.editor_call, m, *a)).grid(
                row=i // 5, column=i % 5, padx=(0, px(3)), pady=(0, px(3)), sticky="ew")
        for column in range(5):
            grid.columnconfigure(column, weight=1)
        # The status on a line of its own: next to the buttons, a long one
        # ("... clipboard 3x3") would widen the whole panel.
        self.selection_label = ttk.Label(tab, text="", style="CardMuted.TLabel", wraplength=px(320),
                                         justify="left")
        self.selection_label.pack(anchor="w", pady=(px(2), 0))
        row = self._row(tab, pady=(px(4), 0))
        ttk.Button(row, text=t("lib.keep"), style="Small.TButton",
                   command=self.on_keep).pack(side="left")
        ttk.Button(row, text=t("panel.sel.export"), style="Small.TButton",
                   command=self.on_export).pack(side="left", padx=(px(4), 0))

    def _build_world(self, notebook) -> None:
        tk, ttk, px, app = self.tk, self.ttk, self.px, self.app
        tab = ttk.Frame(notebook, style="Card.TFrame", padding=(px(10), px(4), px(10), px(10)))
        notebook.add(tab, text=t("panel.tab.world"))

        self._section(tab, t("worlds"))
        row = self._row(tab)
        self.world_list = ttk.Combobox(row, state="readonly")
        self.world_list.pack(side="left", fill="x", expand=True)
        self.world_list.bind("<<ComboboxSelected>>", lambda _e: self._on_world_pick())
        ttk.Button(row, text=t("worlds.load"), style="Accent.TButton",
                   command=self.on_world_load).pack(side="left", padx=(px(6), 0))
        row = self._row(tab, pady=(px(6), 0))
        self.world_name_var = tk.StringVar(master=self.root)
        self.world_entry = ttk.Entry(row, textvariable=self.world_name_var)
        self.world_entry.pack(side="left", fill="x", expand=True)
        self.world_entry.bind("<Return>", lambda _e: self.on_world_save())
        self.world_hint = ttk.Label(row, text=t("worlds.name"), style="Placeholder.TLabel")
        self.world_hint.place(in_=self.world_entry, x=px(8), rely=0.5, anchor="w")
        self.world_hint.bind("<Button-1>", lambda _e: self.world_entry.focus_set())
        self.world_name_var.trace_add("write", lambda *_: self._world_hint())
        ttk.Button(row, text=t("worlds.save"), command=self.on_world_save).pack(side="left", padx=(px(6), 0))
        row = self._row(tab, pady=(px(6), 0))
        for label, command in ((t("worlds.delete"), self.on_world_delete),
                               (t("worlds.folder"), lambda: app.call(app.open_worlds_folder)),
                               (t("worlds.import"), self.on_load_world),
                               (t("worlds.export"), self.on_save_world)):
            ttk.Button(row, text=label, style="Small.TButton", command=command).pack(
                side="left", padx=(0, px(4)))
        self.world_size = ttk.Label(tab, text="", style="CardMuted.TLabel")
        self.world_size.pack(anchor="w", pady=(px(4), 0))
        self._worlds: list = []
        self._world_shown = None

        self._section(tab, t("panel.tab.world"))
        row = self._row(tab)
        ttk.Button(row, text=t("panel.clear"), command=lambda: app.call(app.clear)).pack(side="left")
        ttk.Button(row, text=t("panel.soup"),
                   command=lambda: app.call(app.reseed, self.density.get())).pack(side="left", padx=px(6))
        row = self._row(tab, pady=(px(8), 0))
        ttk.Label(row, text=t("panel.density"), style="Card.TLabel").pack(side="left")
        self.density_value = ttk.Label(row, text="", style="CardMuted.TLabel", width=5, anchor="e")
        self.density_value.pack(side="right")
        self.density = tk.DoubleVar(master=self.root, value=0.16)
        scale = ttk.Scale(row, from_=0.02, to=0.7, variable=self.density,
                          command=lambda v: self.density_value.configure(text=f"{float(v):.0%}"))
        scale.pack(side="left", fill="x", expand=True, padx=px(8))

        self._section(tab, t("panel.skip"))
        row = self._row(tab)
        self.skip_value = tk.StringVar(master=self.root, value="1000")
        entry = ttk.Entry(row, textvariable=self.skip_value, width=10)
        entry.pack(side="left")
        entry.bind("<Return>", lambda _e: self.on_skip())
        ttk.Button(row, text=t("panel.skip.go"), style="Small.TButton", command=self.on_skip).pack(
            side="left", padx=px(6))
        self.skip_progress = ttk.Label(row, text="", style="CardMuted.TLabel")
        self.skip_progress.pack(side="left", padx=px(4))

        self._section(tab, t("panel.rule"))
        self.rule = ttk.Combobox(tab, values=[label for label, _ in RULES])
        self.rule.pack(fill="x")
        self.rule.bind("<<ComboboxSelected>>", lambda _e: self.on_rule())
        self.rule.bind("<Return>", lambda _e: self.on_rule())
        self.fill_worlds()

    def _build_look(self, notebook) -> None:
        tk, ttk, px, app = self.tk, self.ttk, self.px, self.app
        tab = ttk.Frame(notebook, style="Card.TFrame", padding=(px(10), px(4), px(10), px(10)))
        notebook.add(tab, text=t("panel.tab.look"))

        self._section(tab, t("panel.zoom"))
        row = self._row(tab)
        ttk.Button(row, text="-", width=3, command=lambda: app.call(app.zoom_by, -1)).pack(side="left")
        self.zoom_label = ttk.Label(row, text="", style="Card.TLabel", width=18, anchor="center")
        self.zoom_label.pack(side="left", padx=px(4))
        ttk.Button(row, text="+", width=3, command=lambda: app.call(app.zoom_by, 1)).pack(side="left")
        ttk.Button(row, text=t("panel.fit"), command=lambda: app.call(app.fit_view)).pack(side="right")

        self._section(tab, t("panel.palette"))
        self.palette = ttk.Combobox(tab, values=list(palettes.names()), state="readonly")
        self.palette.pack(fill="x")
        self.palette.bind("<<ComboboxSelected>>", lambda _e: app.call(app.set_palette, self.palette.get()))
        self.cycle = tk.BooleanVar(master=self.root, value=True)
        self.cycle_check = ttk.Checkbutton(tab, text="", variable=self.cycle,
                                           command=lambda: app.call(app.set_cycle, self.cycle.get()))
        self.cycle_check.pack(anchor="w", pady=(px(6), 0))

        self._section(tab, t("panel.trails"))
        row = self._row(tab)
        self.trails = tk.DoubleVar(master=self.root, value=1.6)
        self.trail_value = ttk.Label(row, text="", style="CardMuted.TLabel", width=6, anchor="e")
        self.trail_value.pack(side="right")
        trail = ttk.Scale(row, from_=0.0, to=6.0, variable=self.trails, command=self.on_trails)
        trail.pack(side="left", fill="x", expand=True)
        trail.bind("<ButtonPress-1>", lambda _e: setattr(self, "_trail_drag", True))
        trail.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_trail_drag", False))

        self.grid_on = tk.BooleanVar(master=self.root, value=True)
        self.smooth = tk.BooleanVar(master=self.root, value=False)
        self.frame_on = tk.BooleanVar(master=self.root, value=True)
        self.startup = tk.BooleanVar(master=self.root, value=False)
        self.pinned = tk.BooleanVar(master=self.root, value=False)
        self._section(tab, t("panel.tab.look"))
        for text, var, command in (
                (t("panel.grid"), self.grid_on, lambda: app.call(app.set_grid, self.grid_on.get())),
                (t("panel.smooth"), self.smooth, lambda: app.call(app.set_smooth, self.smooth.get())),
                (t("panel.frame"), self.frame_on, lambda: app.call(app.set_editor_frame, self.frame_on.get())),
                (t("panel.startup"), self.startup, lambda: app.call(app.set_startup, self.startup.get())),
                (t("panel.pin"), self.pinned, self.on_pin)):
            ttk.Checkbutton(tab, text=text, variable=var, command=command).pack(anchor="w")
        row = self._row(tab, pady=(px(8), 0))
        self.visible_button = ttk.Button(row, text="", command=lambda: app.call(app.toggle_wallpaper_visible))
        self.visible_button.pack(side="left")
        ttk.Button(row, text=t("panel.save"), command=lambda: app.call(app.save_settings)).pack(side="right")
        self.monitors_label = ttk.Label(tab, text="", style="CardMuted.TLabel", wraplength=px(320),
                                        justify="left")
        self.monitors_label.pack(anchor="w", pady=(px(8), 0))

    # -- the library -------------------------------------------------------------
    def on_open_as_world(self) -> None:
        entry = self.library.selected()
        if entry is not None:
            self.app.call(self.app.load_pattern_as_world, entry.key)

    def on_refresh_library(self) -> None:
        count = self.library.refresh_user()
        self.library.show_category("mine")
        if not count:
            self.message.configure(text=t("lib.mine.empty"))

    def on_keep(self) -> None:
        from tkinter import simpledialog
        if not self.app.snapshot()["selection"]:
            self.message.configure(text=t("panel.sel.empty"))
            return
        name = simpledialog.askstring(t("lib.keep"), t("lib.keep.name"), parent=self.root)
        if name is not None:
            self.app.call(self.app.keep_selection, name)

    # -- saved worlds ------------------------------------------------------------------
    def _label_of(self, item) -> str:
        if item.starter:
            return t("worlds.starter", name=item.name)
        # Two files can share a name (glider.rle, glider.cells): show which is which.
        return item.name if item.path.suffix.lower() == ".rle" else item.path.name

    def fill_worlds(self, select: str | None = None) -> None:
        try:
            self._worlds = worlds.list_worlds(language())
        except Exception:                          # a broken folder must not take the panel down
            self.app.log_exception("reading the worlds folder")
            self._worlds = []
        labels = [self._label_of(item) for item in self._worlds]
        self.world_list.configure(values=labels or [t("worlds.none")])
        current = select if select is not None else self.world_list.get()
        if current in labels:
            self.world_list.set(current)
        elif select and any(i.name == select for i in self._worlds):
            self.world_list.set(next(self._label_of(i) for i in self._worlds if i.name == select))
        elif labels:
            name = self.app.snapshot().get("world_name") or ""
            match = next((self._label_of(i) for i in self._worlds if i.name == name), labels[0])
            self.world_list.set(match)
        else:
            self.world_list.set(t("worlds.none"))

    def _picked_world(self):
        index = self.world_list.current()           # by position: two labels can be alike
        return self._worlds[index] if 0 <= index < len(self._worlds) else None

    def _on_world_pick(self) -> None:
        item = self._picked_world()
        if item is not None and not item.starter:
            self.world_name_var.set(item.name)

    def _world_hint(self) -> None:
        if self.world_name_var.get():
            self.world_hint.place_forget()
        else:
            self.world_hint.place(in_=self.world_entry, x=self.px(8), rely=0.5, anchor="w")

    def on_world_load(self) -> None:
        item = self._picked_world()
        if item is not None:
            self.app.call(self.app.load_world_file, str(item.path), item.name)

    def on_world_save(self) -> None:
        from tkinter import messagebox
        name = self.world_name_var.get().strip()
        if not name:
            self.world_entry.focus_set()
            return
        path = worlds.path_for(name)
        if path.exists() and not messagebox.askyesno(t("worlds"), t("worlds.confirm.overwrite", name=path.stem),
                                                     parent=self.root):
            return
        self.app.call(self.app.save_world_named, name)          # it posts "worlds" when saved

    def on_world_delete(self) -> None:
        from tkinter import messagebox
        item = self._picked_world()
        if item is None:
            return
        if item.starter:
            self.message.configure(text=t("worlds.cannot_delete"))
            return
        if messagebox.askyesno(t("worlds"), t("worlds.confirm.delete", name=item.name), parent=self.root):
            self.app.call(self.app.delete_world, str(item.path))

    # -- commands -----------------------------------------------------------------
    def on_speed(self, value: str) -> None:
        if self._syncing:
            return
        speed = _slider_to_speed(float(value))
        self.speed_value.configure(text=t("panel.per_second", gps=speed))
        self.app.call(self.app.set_speed, speed)

    def on_trails(self, value: str) -> None:
        seconds = round(float(value), 1)
        self.trail_value.configure(text=t("panel.trails.value", s=seconds))
        if not self._syncing:
            self.app.call(self.app.set_trails, seconds)

    def on_brush(self) -> None:
        try:
            size = int(self.brush.get())
        except (self.tk.TclError, ValueError):
            return
        self.app.call(self.app.editor_call, "set_brush", size)

    def on_skip(self) -> None:
        try:
            count = int(float(self.skip_value.get().replace(",", "").replace(".", "").replace("_", "")))
        except (ValueError, OverflowError):             # "abc", "inf", "1e400"
            self.skip_progress.configure(text=t("panel.skip.bad"))
            return
        self.app.call(self.app.skip, count)

    def on_rule(self) -> None:
        text = self.rule.get().strip()
        for label, rule in RULES:
            if text == label:
                text = rule
                break
        if "(" in text and text.endswith(")"):
            text = text[text.rfind("(") + 1:-1]
        self.app.call(self.app.set_rule, text)

    def on_pin(self) -> None:
        self.root.attributes("-topmost", bool(self.pinned.get()))

    def _ask_open(self) -> str | None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.root, filetypes=PATTERN_FILES)
        if not path:
            return None
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
                return handle.read(64 * 1024 * 1024)
        except OSError as exc:
            self.message.configure(text=str(exc))
            return None

    def on_import(self) -> None:
        text = self._ask_open()
        if text is not None:
            self.app.call(self.app.editor_call, "paste_text", text)

    def on_export(self) -> None:
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".rle",
                                            filetypes=(("RLE", "*.rle"), ("All files", "*.*")))
        if path:
            self.app.call(self.app.editor_call, "export_selection", path)

    def on_save_world(self) -> None:
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".rle",
                                            filetypes=(("RLE", "*.rle"), ("All files", "*.*")))
        if path:
            self.app.call(self.app.save_world_as, path)

    def on_load_world(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.root, filetypes=PATTERN_FILES)
        if path:
            self.app.call(self.app.load_world_file, path)

    def on_done(self) -> None:
        self.app.call(self.app.set_interactive, False)

    # -- keyboard -----------------------------------------------------------------
    def _focus(self):
        """The focused widget, or None -- also while a combobox list is open,
        whose popdown is not in tkinter's widget tree and makes focus_get raise."""
        try:
            return self.root.focus_get()
        except (KeyError, self.tk.TclError):
            return None

    def _typing(self) -> bool:
        focus = self._focus()
        return isinstance(focus, (self.tk.Entry, self.ttk.Entry, self.ttk.Combobox, self.ttk.Spinbox))

    def _bind_keys(self) -> None:
        app = self.app

        def key(action):
            def handler(_event):
                if self._typing():
                    return None
                action()
                return "break"
            return handler

        def editor(method, *args):
            return lambda: app.call(app.editor_call, method, *args)

        bind = self.root.bind
        bind("<Escape>", lambda _e: self.on_done())
        bind("<space>", key(lambda: app.call(app.toggle_pause)))
        bind("<Delete>", key(editor("delete_selection")))
        for k in ("<r>", "<R>"):
            bind(k, key(editor("rotate_by", 1)))
        for k in ("<f>", "<F>"):
            bind(k, key(editor("flip_current", "h")))
        for k in ("<plus>", "<equal>", "<KP_Add>"):
            bind(k, key(lambda: app.call(app.zoom_by, 1)))
        for k in ("<minus>", "<KP_Subtract>"):
            bind(k, key(lambda: app.call(app.zoom_by, -1)))
        bind("<Control-c>", key(editor("copy_selection")))
        bind("<Control-x>", key(editor("cut_selection")))
        bind("<Control-v>", key(editor("paste")))
        bind("<Control-a>", key(editor("select_all_visible")))
        bind("<Control-z>", key(editor("undo")))
        bind("<b>", key(lambda: self._set_tool(TOOL_BRUSH)))
        bind("<p>", key(lambda: self._set_tool(TOOL_PATTERN)))
        bind("<s>", key(lambda: self._set_tool(TOOL_SELECT)))
        for digit in range(1, 10):
            bind(str(digit), key(lambda i=digit - 1: self.library.pick_index(i)))

    def _set_tool(self, tool: str) -> None:
        self.tool.set(tool)
        self.app.call(self.app.editor_call, "set_tool", tool)

    # -- showing ------------------------------------------------------------------
    def _fit_height(self, available: int) -> None:
        """Shorten the pattern list until the panel fits the work area."""
        rows = self.library.rows
        while rows > 3 and self.root.winfo_reqheight() > available:
            rows -= 1
            self.library.fit_rows(rows)
            self.root.update_idletasks()

    def show(self) -> None:
        root = self.root
        self.refresh(force=True)
        root.update_idletasks()
        if not self._shown:
            saved = self.app.panel_position()
            left, top, right, bottom = w.work_area(saved)
            self._fit_height(bottom - top - self.px(48))
        width, height = root.winfo_reqwidth(), root.winfo_reqheight()
        if not self._shown:
            margin = self.px(16)
            if saved and left <= saved[0] <= right - 80 and top <= saved[1] <= bottom - 80:
                x, y = saved
            else:
                x, y = right - width - margin, bottom - height - margin
            x = max(left, min(x, right - width))
            y = max(top, min(y, bottom - height))
            root.geometry(f"+{x}+{y}")
            self._shown = True
        root.deiconify()
        root.lift()
        try:
            root.focus_force()
        except self.tk.TclError:
            pass
        if self.frame_hwnd:
            w.SetForegroundWindow(self.frame_hwnd)

    def hide(self) -> None:
        if self.root.state() != "withdrawn":
            try:
                self.app.call(self.app.remember_panel, self.root.winfo_rootx(), self.root.winfo_rooty())
            except self.tk.TclError:
                pass
        self.root.withdraw()

    # -- the two timers ----------------------------------------------------------
    def _poll(self) -> None:
        if not self._alive:
            return
        try:
            while True:
                message = self.inbox.get_nowait()
                kind = message[0]
                if kind == "show":
                    self.show()
                elif kind == "hide":
                    self.hide()
                elif kind == "refresh":
                    self.refresh(force=True)
                elif kind == "tab":
                    self.notebook.select(int(message[1]))
                elif kind == "accent":
                    self.accent = message[1]
                    theme.apply(self.root, self.scale, self.accent)
                    self.library.on_accent()
                elif kind == "library":
                    self.library.refresh_user()
                elif kind == "call":                   # run something on this thread (tests)
                    message[1](self)
                elif kind == "worlds":
                    self.fill_worlds(select=message[1] if len(message) > 1 else None)
                elif kind == "quit":
                    self._alive = False
                    self.hide()
                    self.root.quit()
                    return
        except queue.Empty:
            pass
        except Exception:
            self.app.log_exception("control panel message")
        # Snappy while open; a hidden panel only needs to notice "show" soon enough.
        self.root.after(30 if self._visible() else 250, self._poll)

    def _visible(self) -> bool:
        try:
            return self.root.state() != "withdrawn"
        except self.tk.TclError:
            return False

    def _tick(self) -> None:
        if not self._alive:
            return
        visible = self._visible()
        try:
            if visible:
                self.refresh()
        except Exception:
            self.app.log_exception("control panel refresh")
        self.root.after(250 if visible else 1000, self._tick)

    def refresh(self, force: bool = False) -> None:
        s = self.app.snapshot()
        self.status.configure(text=t("panel.status", gen=num(s["generation"]), pop=num(s["population"]),
                                     fps=num(s["fps"])))
        self.play_button.configure(text=t("panel.play") if s["paused"] else t("panel.pause"))
        self.undo_button.state(["!disabled"] if s["can_undo"] else ["disabled"])
        if force or not self._speed_drag:
            self._syncing = True
            try:
                if force or abs(_speed_to_slider(s["gps"]) - float(self.speed.get())) > 0.5:
                    self.speed.set(_speed_to_slider(s["gps"]))
            finally:
                self._syncing = False
            self.speed_value.configure(text=t("panel.per_second", gps=s["gps"]))
        if force or not self._trail_drag:
            self._syncing = True
            try:
                if force or abs(self.trails.get() - s["trails"]) > 0.05:
                    self.trails.set(s["trails"])
            finally:
                self._syncing = False
            self.trail_value.configure(text=t("panel.trails.value", s=s["trails"]))
        if force:
            self.density.set(s["density"])
            self.density_value.configure(text=f"{s['density']:.0%}")
        self.tool.set(s["tool"])
        if force or self._focus() is not self.brush_spin:
            if self.brush.get() != s["brush"]:
                self.brush.set(s["brush"])
        zoom = s["zoom"]
        self.zoom_label.configure(text=t("panel.zoom.out", k=round(1 / zoom)) if zoom < 1
                                  else t("panel.zoom.value", z=zoom))
        if force or self._focus() is not self.palette:
            if self.palette.get() != s["palette"]:
                self.palette.set(s["palette"])
        self.cycle.set(s["cycle"])
        self.cycle_check.configure(text=t("panel.cycle", m=s["cycle_minutes"]))
        self.grid_on.set(s["grid"])
        self.smooth.set(s["smooth"])
        self.frame_on.set(s["frame"])
        self.startup.set(s["startup"])
        if force or self._focus() is not self.rule:
            label = next((lbl for lbl, rule in RULES if rule == s["rule"]), s["rule"])
            if self.rule.get() != label:
                self.rule.set(label)
        self.visible_button.configure(text=t("panel.show") if not s["visible"] else t("panel.hide"))
        if s["selection"]:
            x, y, sw, sh = s["selection"]
            text = t("panel.sel.info", w=sw, h=sh, x=x, y=y)
        else:
            text = t("panel.sel.empty")
        if s["clipboard"]:
            text += "   " + t("panel.clip", w=s["clipboard"][1], h=s["clipboard"][0])
        self.selection_label.configure(text=text)
        if s["skip_remaining"]:
            eta = s["skip_remaining"] / s["skip_rate"] if s["skip_rate"] else 0
            left = num(s["skip_remaining"])
            self.skip_progress.configure(text=t("panel.skip.eta", n=left, s=num(eta))
                                         if eta > 1 else t("panel.skip.left", n=left))
        else:
            self.skip_progress.configure(text="")
        self.monitors_label.configure(text=t("panel.monitors", n=s["monitors"], layer=t(f"layer.{s['layer']}")))
        size = s["world_size"]
        self.world_size.configure(text=t("worlds.size", w=num(size[0]), h=num(size[1])))
        if s["world_name"] != self._world_shown:
            self._world_shown = s["world_name"]
            match = next((self._label_of(i) for i in self._worlds if i.name == s["world_name"]), None)
            if match is not None and self._focus() is not self.world_list:
                self.world_list.set(match)
        if s["message_serial"] != self._message_serial:
            self._message_serial = s["message_serial"]
            self.message.configure(text=s["message"])

    def run(self) -> None:
        self.root.mainloop()

    def dispose(self) -> None:
        self._alive = False
        try:
            self.root.destroy()
        except Exception:
            pass
        for name in list(vars(self)):
            if name not in ("host", "app", "inbox"):
                setattr(self, name, None)
