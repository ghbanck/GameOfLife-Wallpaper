"""A dark ttk theme for the control panel, scaled for the monitor's DPI.

Built on ``clam`` because it is the one stock theme whose colours can all be
changed.  The accent follows the running palette, so the panel always looks
like part of the wallpaper it is editing.
"""

from __future__ import annotations

import base64
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

import numpy as np

BG = "#0f1217"
SURFACE = "#161a21"
RAISED = "#1e242e"
HOVER = "#262d39"
BORDER = "#2c3441"
TEXT = "#e6edf3"
MUTED = "#8d98a6"
DIM = "#5c6674"
DANGER = "#ff6b6b"


def hex_colour(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(c))) for c in rgb)


def readable_on(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return "#0b0f14" if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else "#ffffff"


# -- small antialiased images for the widgets clam cannot draw nicely ------------
def _rgb(text: str) -> tuple[int, int, int]:
    return tuple(int(text[i:i + 2], 16) for i in (1, 3, 5))


def _raster(size: int, draw, supersample: int = 4) -> np.ndarray:
    big = size * supersample
    yy, xx = (np.mgrid[0:big, 0:big] + 0.5) / supersample
    layers = draw(xx, yy, float(size))
    rgba = np.zeros((big, big, 4))
    for mask, colour in layers:                          # painter's order, bottom first
        a = mask.astype(float)[..., None]
        rgba[..., :3] = np.array(colour) / 255.0 * a + rgba[..., :3] * (1 - a)
        rgba[..., 3:] = a + rgba[..., 3:] * (1 - a)
    rgba = rgba.reshape(size, supersample, size, supersample, 4).mean(axis=(1, 3))
    out = np.zeros((size, size, 4), np.uint8)
    alpha = rgba[..., 3]                                 # colours are premultiplied until here
    safe = np.maximum(alpha, 1e-6)[..., None]
    out[..., :3] = (rgba[..., :3] / safe * 255).clip(0, 255)
    out[..., 3] = (alpha * 255).clip(0, 255)
    return out


def _rounded(xx, yy, x0, y0, x1, y1, radius):
    cx = np.clip(xx, x0 + radius, x1 - radius)
    cy = np.clip(yy, y0 + radius, y1 - radius)
    return np.hypot(xx - cx, yy - cy) <= radius


def _segment(xx, yy, a, b):
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    t = np.clip(((xx - ax) * dx + (yy - ay) * dy) / (dx * dx + dy * dy), 0.0, 1.0)
    return np.hypot(xx - (ax + t * dx), yy - (ay + t * dy))


def _image_data(scale: float, accent: tuple[int, int, int]) -> dict[str, str]:
    from .icon import png_bytes
    box = max(12, round(14 * scale))
    knob = max(12, round(14 * scale))
    hot = tuple(min(255, int(c * 1.15 + 12)) for c in accent)
    mark = _rgb(readable_on(accent))

    def unchecked(xx, yy, s):
        return [(_rounded(xx, yy, 0.5, 0.5, s - 0.5, s - 0.5, s * 0.22), _rgb("#4a5566")),
                (_rounded(xx, yy, 1.5, 1.5, s - 1.5, s - 1.5, s * 0.18), _rgb(RAISED))]

    def checked(xx, yy, s):
        tick = np.minimum(_segment(xx, yy, (s * 0.26, s * 0.53), (s * 0.43, s * 0.70)),
                          _segment(xx, yy, (s * 0.43, s * 0.70), (s * 0.76, s * 0.32)))
        return [(_rounded(xx, yy, 0.5, 0.5, s - 0.5, s - 0.5, s * 0.22), accent),
                (tick <= s * 0.075, mark)]

    def dot(colour):
        return lambda xx, yy, s: [(np.hypot(xx - s / 2, yy - s / 2) <= s / 2 - 0.5, colour)]

    images = {"box_off": _raster(box, unchecked), "box_on": _raster(box, checked),
              "knob": _raster(knob, dot(accent)), "knob_hot": _raster(knob, dot(hot))}
    return {name: base64.b64encode(png_bytes(img)).decode() for name, img in images.items()}


def _custom_elements(root: tk.Misc, style: ttk.Style, scale: float, accent: tuple[int, int, int]) -> None:
    data = _image_data(scale, accent)
    images = getattr(root, "_golwall_images", None)
    if images is None:
        images = {name: tk.PhotoImage(master=root, data=value) for name, value in data.items()}
        root._golwall_images = images                      # Tk images die with their last reference
        width = max(12, round(14 * scale)) + max(4, round(6 * scale))
        style.element_create("Golwall.Checkbutton.indicator", "image", images["box_off"],
                             ("selected", images["box_on"]), width=width, sticky="w")
        style.element_create("Golwall.Scale.slider", "image", images["knob"],
                             ("pressed", images["knob_hot"]), ("active", images["knob_hot"]))
    else:
        for name, value in data.items():
            images[name].configure(data=value)
    style.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
        ("Golwall.Checkbutton.indicator", {"side": "left", "sticky": ""}),
        ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
            ("Checkbutton.label", {"sticky": "nswe"})]})]})])
    style.layout("Horizontal.TScale", [("Horizontal.Scale.focus", {"sticky": "nswe", "children": [
        ("Horizontal.Scale.padding", {"sticky": "nswe", "children": [
            ("Horizontal.Scale.trough", {"sticky": "nswe", "children": [
                ("Golwall.Scale.slider", {"side": "left", "sticky": ""})]})]})]})])


def apply(root: tk.Misc, scale: float, accent_rgb: tuple[int, int, int]) -> ttk.Style:
    px = lambda v: max(1, int(round(v * scale)))                           # noqa: E731
    accent = hex_colour(accent_rgb)
    accent_hover = hex_colour(tuple(min(255, int(c * 1.15 + 12)) for c in accent_rgb))
    on_accent = readable_on(accent_rgb)

    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(name, root=root).configure(family="Segoe UI", size=9)
        except tk.TclError:
            pass
    title_font = ("Segoe UI Semibold", 11)
    small_font = ("Segoe UI", 8)
    mono_font = ("Cascadia Mono", 8) if "Cascadia Mono" in tkfont.families(root) else ("Consolas", 8)

    root.configure(background=BG)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT, bordercolor=BORDER, darkcolor=BG,
                    lightcolor=BG, troughcolor=RAISED, focuscolor=accent, selectbackground=accent,
                    selectforeground=on_accent, fieldbackground=RAISED, insertcolor=TEXT,
                    font=("Segoe UI", 9))
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=SURFACE)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Card.TLabel", background=SURFACE, foreground=TEXT)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=small_font)
    style.configure("CardMuted.TLabel", background=SURFACE, foreground=MUTED, font=small_font)
    style.configure("Title.TLabel", background=BG, foreground=TEXT, font=title_font)
    style.configure("Section.TLabel", background=SURFACE, foreground=MUTED, font=("Segoe UI Semibold", 8))
    style.configure("Stat.TLabel", background=BG, foreground=MUTED, font=mono_font)
    style.configure("Accent.TLabel", background=BG, foreground=accent, font=small_font)
    style.configure("Placeholder.TLabel", background=RAISED, foreground=DIM, font=("Segoe UI", 9))

    button_pad = (px(10), px(4))
    style.configure("TButton", background=RAISED, foreground=TEXT, bordercolor=BORDER,
                    lightcolor=RAISED, darkcolor=RAISED, focusthickness=0, padding=button_pad,
                    relief="flat")
    style.map("TButton", background=[("disabled", SURFACE), ("pressed", BORDER), ("active", HOVER)],
              foreground=[("disabled", DIM)], bordercolor=[("focus", accent)])
    style.configure("Accent.TButton", background=accent, foreground=on_accent, bordercolor=accent,
                    lightcolor=accent, darkcolor=accent, font=("Segoe UI Semibold", 9))
    style.map("Accent.TButton", background=[("pressed", accent), ("active", accent_hover)],
              foreground=[("disabled", DIM)])
    style.configure("Small.TButton", padding=(px(6), px(2)))
    style.configure("Tool.TButton", padding=(px(6), px(3)))

    for widget in ("TCheckbutton", "TRadiobutton"):
        style.configure(widget, background=SURFACE, foreground=TEXT, indicatorbackground=RAISED,
                        indicatorforeground=on_accent, indicatormargin=(0, 0, px(6), 0),
                        indicatorsize=px(12), padding=(0, px(2)), focusthickness=0)
        style.map(widget, background=[("active", SURFACE)],
                  indicatorbackground=[("selected", accent), ("pressed", HOVER)],
                  foreground=[("disabled", DIM)])
    style.configure("Toggle.TRadiobutton", background=RAISED, foreground=TEXT, anchor="center",
                    padding=(px(8), px(4)), indicatorsize=0, indicatormargin=0)
    style.map("Toggle.TRadiobutton", background=[("selected", accent), ("active", HOVER)],
              foreground=[("selected", on_accent)])
    style.layout("Toggle.TRadiobutton", [("Radiobutton.padding", {"sticky": "nswe", "children": [
        ("Radiobutton.label", {"sticky": "nswe"})]})])

    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, px(4), 0, 0))
    style.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=(px(12), px(5)),
                    borderwidth=0, font=("Segoe UI Semibold", 9))
    style.map("TNotebook.Tab", background=[("selected", SURFACE), ("active", RAISED)],
              foreground=[("selected", TEXT)], lightcolor=[("selected", SURFACE)],
              bordercolor=[("selected", SURFACE)])

    style.configure("TCombobox", fieldbackground=RAISED, background=RAISED, foreground=TEXT,
                    arrowcolor=MUTED, bordercolor=BORDER, lightcolor=RAISED, darkcolor=RAISED,
                    padding=(px(6), px(3)), arrowsize=px(12))
    style.map("TCombobox", fieldbackground=[("readonly", RAISED)], foreground=[("readonly", TEXT)],
              selectbackground=[("readonly", RAISED)], selectforeground=[("readonly", TEXT)],
              bordercolor=[("focus", accent)], arrowcolor=[("active", TEXT)])
    root.option_add("*TCombobox*Listbox.background", RAISED)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", accent)
    root.option_add("*TCombobox*Listbox.selectForeground", on_accent)
    root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 9))

    for widget in ("TEntry", "TSpinbox"):
        style.configure(widget, fieldbackground=RAISED, foreground=TEXT, bordercolor=BORDER,
                        lightcolor=RAISED, darkcolor=RAISED, insertcolor=TEXT, padding=(px(6), px(3)),
                        arrowcolor=MUTED, arrowsize=px(10), background=RAISED)
        style.map(widget, bordercolor=[("focus", accent)])

    # clam's scale trough borrows the slider's colours for its outline, so the
    # slider is an image of our own and the trough is left a flat groove.
    style.configure("Horizontal.TScale", background=SURFACE, troughcolor=RAISED, bordercolor=SURFACE,
                    lightcolor=SURFACE, darkcolor=SURFACE, gripcount=0)
    _custom_elements(root, style, scale, accent_rgb)
    style.configure("Vertical.TScrollbar", background=RAISED, troughcolor=SURFACE, bordercolor=SURFACE,
                    lightcolor=RAISED, darkcolor=RAISED, arrowcolor=MUTED, arrowsize=px(12),
                    gripcount=0)
    style.map("Vertical.TScrollbar", background=[("active", HOVER)])
    style.configure("TSeparator", background=BORDER)

    # The pattern library: a flat list whose rows are tall enough for an icon.
    style.configure("Library.Treeview", background=RAISED, fieldbackground=RAISED, foreground=TEXT,
                    bordercolor=RAISED, lightcolor=RAISED, darkcolor=RAISED, borderwidth=0,
                    rowheight=px(30), font=("Segoe UI", 9), indent=0)
    style.map("Library.Treeview", background=[("selected", accent)], foreground=[("selected", on_accent)])
    style.layout("Library.Treeview", [("Treeview.field", {"sticky": "nswe", "border": 0, "children": [
        ("Treeview.padding", {"sticky": "nswe", "children": [("Treeview.treearea", {"sticky": "nswe"})]})]})])
    style.layout("Library.Treeview.Item", [("Treeitem.padding", {"sticky": "nswe", "children": [
        ("Treeitem.image", {"side": "left", "sticky": ""}),
        ("Treeitem.text", {"side": "left", "sticky": ""})]})])
    return style


def style_listbox(listbox: tk.Listbox, accent_rgb: tuple[int, int, int]) -> None:
    listbox.configure(background=RAISED, foreground=TEXT, selectbackground=hex_colour(accent_rgb),
                      selectforeground=readable_on(accent_rgb), highlightthickness=0,
                      borderwidth=0, activestyle="none", font=("Segoe UI", 9), relief="flat")
