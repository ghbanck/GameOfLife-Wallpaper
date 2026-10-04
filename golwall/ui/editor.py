"""The editor: tools, selection, clipboard and undo.  No window of its own.

The wallpaper is already on screen behind the icons, so a stroke is visible
the moment it is drawn; the editor only has to turn pointer events into edits
and describe what to overlay -- the ghost of the pattern under the pointer,
the selection box -- for the renderer to draw in the same frame.

Everything here runs on the main thread.  Pointer events arrive from the
desktop mouse hook (or, in windowed mode, from the window itself) already in
camera pixels; the control panel reaches in through ``app.call``.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from ..capabilities.input import MIDDLE, RIGHT
from ..core import library, rle
from ..native import win32 as w
from ..render.renderer import Box

TOOL_PATTERN, TOOL_BRUSH, TOOL_SELECT = "pattern", "brush", "select"

GHOST = (1.0, 1.0, 0.67, 0.92)
SELECT_EDGE = (1.0, 0.86, 0.47, 1.0)
SELECT_FILL = (1.0, 0.86, 0.47, 0.12)
ERASE_EDGE = (1.0, 0.43, 0.43, 1.0)
BRUSH_FILL = (1.0, 1.0, 0.67, 0.22)
ERASE_FILL = (1.0, 0.43, 0.43, 0.22)
UNDO_DEPTH = 40
UNDO_BYTES = 192 * 1024 * 1024      # a 7680x7946 world is 7.6 MB a snapshot
WHEEL_DELTA = 120


class Editor:
    def __init__(self, app) -> None:
        self.app = app
        self.tool = TOOL_PATTERN
        self.pattern_key = "glider"
        self.rotate = 0
        self.flip = False
        self.brush_size = max(1, int(app.cfg.brush_size))
        self.selection: tuple[int, int, int, int] | None = None
        self.clipboard: np.ndarray | None = None
        self.message = ""
        self.version = 0                 # bumps whenever the overlay changes
        self.mask_version = 0

        self._drag: str | None = None
        self._buttons = 0
        self._anchor = (0, 0)            # unwrapped cell a selection drag started on
        self._select_before = None
        self._last: tuple[int, int] | None = None     # unwrapped cell of the last pointer event
        self._grab = (0, 0)              # where in the floating cells the pointer holds them
        self._pan_from = (0, 0)
        self._float: np.ndarray | None = None
        self._float_at = (0, 0)
        self._float_moved = False        # lifted out of the world, not pasted in
        self._clip_text = ""             # what we last put on the Windows clipboard
        self._cursor: tuple[int, int] | None = None
        self._art_key = None
        self._art: np.ndarray | None = None
        self._wheel = 0
        self._undo: deque = deque(maxlen=UNDO_DEPTH)

    # -- bookkeeping ------------------------------------------------------
    def _changed(self) -> None:
        self.version += 1

    def _say(self, text: str) -> None:
        self.message = text
        self.app.say(text)
        self._changed()

    def _touch(self) -> None:
        """The world was edited: the overlay and the frame need redrawing."""
        self._changed()

    def checkpoint(self, extra: dict | None = None) -> None:
        """Remember the world for undo; ``extra`` is the app's own state that goes with it
        (the saved world's name, speed, camera) when a whole world is being replaced."""
        self._undo.append((*self.app.world.snapshot(), extra))
        # Bounded by memory as well as by count: forty snapshots of a huge
        # world would be hundreds of megabytes.
        while len(self._undo) > 1 and sum(snap[0].nbytes for snap in self._undo) > UNDO_BYTES:
            self._undo.popleft()

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._drop_float(commit=False)
        snap = self._undo.pop()
        world = self.app.world
        size = (world.w, world.h)
        if world.restore(snap[:3]):
            if (world.w, world.h) != size:
                self.app.world_resized()
            if len(snap) > 3 and snap[3]:
                self.app.restore_world_state(snap[3])
            self.app.renderer_reset(mature=True)
            self._touch()
        return True

    def world_resized(self) -> None:
        """The world changed size: selections and floating cells no longer mean anything."""
        self._float = None
        self._drag = None
        self._buttons = 0
        self.selection = None
        self._cursor = None
        self._last = None
        self.mask_version += 1
        self._changed()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    # -- what is being drawn ----------------------------------------------
    def current_art(self) -> np.ndarray:
        """The selected pattern with the current rotation and flip applied."""
        key = (self.pattern_key, self.rotate % 4, self.flip)
        if key != self._art_key or self._art is None:
            try:
                art = library.cells(self.pattern_key)
            except (KeyError, OSError, rle.PatternError):
                self.pattern_key = "glider"            # a user file that went away
                art = library.cells("glider")
            if self.rotate % 4:
                art = np.rot90(art, self.rotate % 4)
            if self.flip:
                art = np.fliplr(art)
            self._art = np.ascontiguousarray(art)
            self._art_key = key
            self.mask_version += 1
        return self._art

    def _brush(self) -> tuple[int, int]:
        """(side, offset) of the brush in cells: the square starts ``offset`` before the pointer.

        Zoomed out to k cells per pixel, the smallest thing on screen is a
        k x k block, so the brush covers whole blocks -- a size-1 eraser then
        clears exactly the pixel it is over.
        """
        k = getattr(self.app.camera, "shrink", 1)
        if k <= 1:
            return self.brush_size, self.brush_size // 2
        blocks = -(-self.brush_size // k)
        return blocks * k, (blocks - 1) // 2 * k

    def _block(self) -> int:
        return max(1, getattr(self.app.camera, "shrink", 1))

    @staticmethod
    def _centred(art: np.ndarray, cell: tuple[int, int]) -> tuple[int, int]:
        h, width = art.shape
        return cell[0] - width // 2, cell[1] - h // 2

    def set_tool(self, tool: str) -> None:
        if tool in (TOOL_PATTERN, TOOL_BRUSH, TOOL_SELECT) and tool != self.tool:
            self._drop_float()
            self.tool = tool
            self._changed()

    def set_pattern(self, key: str) -> None:
        if library.has(key):
            self._drop_float()
            self.pattern_key = key
            self.tool = TOOL_PATTERN
            self._changed()

    def set_brush(self, size: int) -> None:
        self.brush_size = max(1, min(64, int(size)))
        self._changed()

    def rotate_by(self, quarter: int = 1) -> None:
        if self._float is not None:
            self._float = np.ascontiguousarray(np.rot90(self._float, -quarter))
            self._grab = (self._float.shape[1] // 2, self._float.shape[0] // 2)
            self.mask_version += 1
        elif self.tool == TOOL_SELECT and self.selection:
            self.transform_selection("rotate")
        else:
            self.rotate = (self.rotate - quarter) % 4
        self._changed()

    def flip_current(self, axis: str = "h") -> None:
        if self._float is not None:
            self._float = np.ascontiguousarray(np.fliplr(self._float) if axis == "h" else np.flipud(self._float))
            self._grab = (self._float.shape[1] // 2, self._float.shape[0] // 2)
            self.mask_version += 1
        elif self.tool == TOOL_SELECT and self.selection:
            self.transform_selection("flip_h" if axis == "h" else "flip_v")
        else:
            self.flip = not self.flip
        self._changed()

    # -- what the renderer overlays ------------------------------------------
    def overlay(self):
        """(boxes, mask array, mask rect, mask colour) for this frame."""
        boxes: list[Box] = []
        under = Box((0, 0, 0, 0), SELECT_EDGE)
        if self.selection is not None:
            under = Box(self.selection, SELECT_EDGE, SELECT_FILL)
        boxes.append(under)
        mask = rect = colour = None
        over = Box((0, 0, 0, 0), SELECT_EDGE)
        if self._float is not None:
            fh, fw = self._float.shape
            mask, rect, colour = self._float, (*self._float_at, fw, fh), GHOST
            over = Box((*self._float_at, fw, fh), SELECT_EDGE)
        elif self._cursor is not None:
            if self.tool == TOOL_PATTERN:
                art = self.current_art()
                x, y = self._centred(art, self._cursor)
                mask, rect, colour = art, (x, y, art.shape[1], art.shape[0]), GHOST
            elif self.tool == TOOL_BRUSH:
                size, offset = self._brush()
                x, y = self._cursor[0] - offset, self._cursor[1] - offset
                erase = self._drag == "erase"
                over = Box((x, y, size, size), ERASE_EDGE if erase else GHOST,
                           ERASE_FILL if erase else BRUSH_FILL)
        boxes.append(over)
        return tuple(boxes), mask, rect, colour

    # -- pointer ------------------------------------------------------------
    def hover(self, point: tuple[int, int] | None) -> None:
        """Where the pointer is (camera pixels), or None when it is off the desktop."""
        if self._drag is not None:
            return                      # a gesture tracks its own position
        cell = None if point is None else self.app.camera.screen_to_world(*point)
        if cell != self._cursor:
            self._cursor = cell
            if self._float is not None and cell is not None:
                self._float_at = (cell[0] - self._grab[0], cell[1] - self._grab[1])
            self._changed()

    def _unwrapped(self, sx: int, sy: int) -> tuple[int, int]:
        """The cell under a screen pixel *before* wrapping.

        World cells jump from the last column back to 0 at the seam, but on
        screen those two cells sit side by side; strokes and selections are
        measured in these continuous coordinates and wrapped only when written.
        """
        return self.app.camera.unwrapped(sx, sy)

    def press(self, button: int, sx: int, sy: int) -> None:
        if self._buttons & button:
            self.release(button, sx, sy)     # already down as far as we know: its release was lost
        cam = self.app.camera
        cell = cam.screen_to_world(sx, sy)
        here = self._unwrapped(sx, sy)
        self._cursor = cell
        self._buttons |= button
        self._changed()
        if self._drag is not None:
            # A second button during a gesture: right cancels a move or a
            # selection in progress, anything else is ignored.
            if button == RIGHT and self._drag in ("move", "select"):
                self._cancel_drag()
            return
        self._last = here
        if button == MIDDLE:
            self._drag, self._pan_from = "pan", (sx, sy)
            return
        erase = button == RIGHT
        if self._float is not None:                   # a paste is waiting to be placed
            if not erase:
                self._drop_float()
            elif self._float_moved:
                self.undo()                           # put the lifted cells back
            else:
                self._drop_float(commit=False)        # abandon the paste
            self._drag = "done"
            return
        if self.tool == TOOL_SELECT:
            if not erase and self.selection and self._inside(cell, self.selection):
                self.checkpoint()
                x, y = self.selection[:2]
                world = self.app.world
                self._grab = ((cell[0] - x) % world.w, (cell[1] - y) % world.h)
                self._pick_up(copy=bool(w.GetAsyncKeyState(0x11) & 0x8000))   # Ctrl copies
                self._drag = "move"
            elif erase:
                self.selection = None
                self._drag = "done"
            else:
                self._select_before = self.selection
                self._anchor = here
                k = self._block()
                self.selection = (cell[0], cell[1], min(k, self.app.world.w), min(k, self.app.world.h))
                self._drag = "select"
            return
        self.checkpoint()
        if self.tool == TOOL_PATTERN:
            art = self.current_art()
            self.app.world.stamp(art, *self._centred(art, cell), erase=erase)
            self._touch()
            self._drag = "done"
            return
        self._drag = "erase" if erase else "draw"
        self._paint_path([here], erase=erase)

    def _cancel_drag(self) -> None:
        if self._drag == "move":
            if self._float_moved:
                self.undo()
            else:
                self._drop_float(commit=False)
        elif self._drag == "select":
            self.selection = self._select_before
        self._drag = "done"               # swallow the rest of the gesture
        self._changed()

    def move(self, sx: int, sy: int) -> None:
        self.move_path([(sx, sy)])

    def move_path(self, points: list[tuple[int, int]]) -> None:
        """Every pointer position since the last frame, oldest first.

        A 1000 Hz mouse sends a move each millisecond; a stroke is painted as
        one polyline per frame rather than one region write per event.
        """
        if not points:
            return
        cam = self.app.camera
        sx, sy = points[-1]
        cell = cam.screen_to_world(sx, sy)
        here = self._unwrapped(sx, sy)
        previous, self._cursor = self._cursor, cell
        last, self._last = self._last, here
        world = self.app.world
        if self._drag == "pan":
            px, py = self._pan_from
            cam.pan_pixels(px - sx, py - sy)
            self._pan_from = (sx, sy)
        elif self._drag in ("draw", "erase"):
            path = [last] if last is not None else []
            path += [self._unwrapped(px, py) for px, py in points]
            self._paint_path(path, erase=self._drag == "erase")
        elif self._drag == "select":
            (ax, ay), (bx, by) = self._anchor, here
            x0, x1 = sorted((ax, bx))
            y0, y1 = sorted((ay, by))
            k = self._block()                  # zoomed out, the far corner's whole block is in
            self.selection = (x0 % world.w, y0 % world.h,
                              min(world.w, x1 - x0 + k), min(world.h, y1 - y0 + k))
        elif self._drag == "move" and self._float is not None:
            gx, gy = self._grab
            self._float_at = (cell[0] - gx, cell[1] - gy)
        if cell != previous:
            self._changed()

    def release(self, button: int, sx: int, sy: int) -> None:
        self._buttons &= ~button
        if self._buttons:
            return
        if self._drag == "move":
            self._drop_float()
        self._drag = None
        self._changed()

    def wheel(self, delta: int, sx: int, sy: int) -> None:
        if self._drag in ("draw", "erase", "select", "move"):
            return          # zooming mid-drag would move the cells under the stroke
        # High-resolution wheels report fractions of a notch; add them up.
        self._wheel += delta
        steps = int(self._wheel / WHEEL_DELTA)
        if steps:
            self._wheel -= steps * WHEEL_DELTA
            if self.app.camera.zoom_by(steps, (sx, sy)):
                self.app.view_changed()
                self._changed()

    def cancel(self) -> None:
        """The editor closed mid-gesture: finish cleanly."""
        if self._drag == "move":
            self._drop_float()
        self._drag = None
        self._buttons = 0
        self._cursor = None
        self._last = None
        self._changed()

    def _inside(self, cell: tuple[int, int], rect: tuple[int, int, int, int]) -> bool:
        world = self.app.world
        x, y, rw, rh = rect
        return (cell[0] - x) % world.w < rw and (cell[1] - y) % world.h < rh

    # -- editing the world ------------------------------------------------
    def _paint_path(self, path: list[tuple[int, int]], erase: bool) -> None:
        """Brush every cell along a polyline of pointer positions, so fast drags join up.

        Each step goes the short way round the torus: positions are unwrapped,
        but a zoom or a pan can still shift them by a whole world, and that
        must not paint a stripe from one edge to the other.
        """
        world = self.app.world
        size, offset = self._brush()
        k = self._block()
        x, y = path[0]
        points = {(x, y)}
        for nx, ny in path[1:]:
            dx = (nx - x + world.w // 2) % world.w - world.w // 2
            dy = (ny - y + world.h // 2) % world.h - world.h // 2
            steps = max(abs(dx), abs(dy))
            if steps:
                t = np.linspace(0.0, 1.0, steps + 1)[1:]
                px = np.rint(x + dx * t).astype(int)
                py = np.rint(y + dy * t).astype(int)
                if k > 1:                      # stay on whole blocks between the pointer's samples
                    px, py = px // k * k, py // k * k
                points.update(zip(px.tolist(), py.tolist()))
            x, y = x + dx, y + dy
        points = sorted(points)
        # One region write for the whole path instead of one per point.
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        left, top = min(xs) - offset, min(ys) - offset
        width, height = max(xs) - min(xs) + size, max(ys) - min(ys) + size
        if width > world.w or height > world.h:
            square = np.ones((size, size), np.uint8)
            for px, py in points:
                world.stamp(square, px - offset, py - offset, erase=erase)
        else:
            block = np.zeros((height, width), np.uint8)
            for px, py in points:
                bx, by = px - offset - left, py - offset - top
                block[by:by + size, bx:bx + size] = 1
            world.put_region(block, left, top, "erase" if erase else "or")
        self._touch()

    def _pick_up(self, copy: bool = False) -> None:
        """Lift the selected cells so they follow the pointer."""
        if self.selection is None:
            return
        x, y, sw, sh = self.selection
        self._float = np.ascontiguousarray(self.app.world.get_region(x, y, sw, sh))
        self._float_at = (x, y)
        self._float_moved = not copy
        self.mask_version += 1
        if not copy:
            self.app.world.clear_region(x, y, sw, sh)
        self._touch()

    def _drop_float(self, commit: bool = True) -> None:
        if self._float is None:
            return
        art, (x, y) = self._float, self._float_at
        self._float = None
        self.mask_version += 1
        if commit:
            world = self.app.world
            world.put_region(art, x, y, "or")
            # Never a selection larger than the world: it would read the torus twice.
            self.selection = (x % world.w, y % world.h, min(art.shape[1], world.w), min(art.shape[0], world.h))
            self.tool = TOOL_SELECT
        self._touch()

    # -- selection commands, also driven from the control panel ---------------
    def select_all_visible(self) -> None:
        vp = self.app.camera.viewport()
        self.selection = (vp.cell_x, vp.cell_y, vp.cols, vp.rows)
        self.tool = TOOL_SELECT
        self._changed()

    def clear_selection(self) -> None:
        self.selection = None
        self._changed()

    def _selected(self) -> np.ndarray | None:
        if self.selection is None:
            return None
        x, y, sw, sh = self.selection
        return self.app.world.get_region(x, y, sw, sh)

    def selected_cells(self) -> np.ndarray | None:
        return self._selected()

    def copy_selection(self) -> None:
        cells = self._selected()
        if cells is None:
            return
        self.clipboard = cells.copy()
        text = rle.to_rle(rle.crop(cells) if cells.any() else cells, self.app.world.rule)
        if w.set_clipboard_text(text, self.app.control.hwnd):
            self._clip_text = text
        self._changed()

    def cut_selection(self) -> None:
        if self.selection is None:
            return
        self.checkpoint()
        self.copy_selection()
        self.app.world.clear_region(*self.selection)
        self._touch()

    def delete_selection(self) -> None:
        if self.selection is None:
            return
        self.checkpoint()
        self.app.world.clear_region(*self.selection)
        self._touch()

    def _float_at_pointer(self, cells: np.ndarray) -> None:
        self._float = np.ascontiguousarray(cells, dtype=np.uint8)
        cell = self._cursor or (self.selection or (*self.app.camera.screen_to_world(
            self.app.camera.screen_w // 2, self.app.camera.screen_h // 2), 0, 0))[:2]
        fh, fw = self._float.shape
        self._grab = (fw // 2, fh // 2)
        self._float_at = (cell[0] - fw // 2, cell[1] - fh // 2)
        self._float_moved = False
        self.mask_version += 1
        self.tool = TOOL_SELECT
        self.checkpoint()
        self._changed()

    def paste(self) -> None:
        """Pick the clipboard up under the pointer; the next click drops it.

        Text copied from elsewhere (an RLE from LifeWiki, say) wins over our
        own clipboard, unless it is just what we put there ourselves.
        """
        text = w.clipboard_text(self.app.control.hwnd) or ""
        if text.strip() and text != self._clip_text:
            if self.paste_text(text, quiet=self.clipboard is not None):
                return
        if self.clipboard is not None:
            world = self.app.world               # the clipboard may be from a bigger world
            self._float_at_pointer(self.clipboard[:world.h, :world.w].copy())
        elif text.strip():
            self.paste_text(text)
        else:
            from .text import t
            self._say(t("panel.nothing_to_paste"))

    def paste_text(self, text: str, quiet: bool = False) -> bool:
        """Parse RLE / plaintext / Life 1.06 and float it under the pointer."""
        from .text import t
        if not text.strip():
            if not quiet:
                self._say(t("panel.nothing_to_paste"))
            return False
        try:
            pattern = rle.parse(text)
        except rle.PatternError as exc:
            if not quiet:
                self._say(t("panel.import.fail", err=exc))
            return False
        cells = rle.crop(pattern.cells)
        if not cells.size:
            if not quiet:
                self._say(t("panel.nothing_to_paste"))
            return False
        world = self.app.world
        cells = cells[:world.h, :world.w]
        self.clipboard = cells.copy()
        self._float_at_pointer(cells)
        self._say(t("panel.import.ok", w=cells.shape[1], h=cells.shape[0]))
        return True

    def export_selection(self, path: str) -> bool:
        cells = self._selected()
        if cells is None:
            return False
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(rle.to_rle(rle.crop(cells) if cells.any() else cells,
                                        self.app.world.rule, name="golwall selection"))
            return True
        except OSError as exc:
            self._say(str(exc))
            return False

    def fill_selection(self, density: float = 0.3) -> None:
        if self.selection is None:
            return
        self.checkpoint()
        x, y, sw, sh = self.selection
        self.app.world.seed_soup(density, (x, y, x + sw, y + sh))
        self._touch()

    def transform_selection(self, op: str) -> None:
        """Rotate (about its centre) or mirror the selected cells in place."""
        if self.selection is None:
            return
        world = self.app.world
        x, y, sw, sh = self.selection
        if op == "rotate" and (sh > world.w or sw > world.h):
            from .text import t
            self._say(t("panel.too_big"))            # it would not fit: never lose cells
            return
        self.checkpoint()
        art = world.get_region(x, y, sw, sh)
        world.clear_region(x, y, sw, sh)
        if op == "rotate":
            art = np.rot90(art, -1)
        elif op == "flip_h":
            art = np.fliplr(art)
        elif op == "flip_v":
            art = np.flipud(art)
        nh, nw = art.shape
        nx, ny = x + (sw - nw) // 2, y + (sh - nh) // 2
        world.put_region(art, nx, ny, "or")
        self.selection = (nx % world.w, ny % world.h, nw, nh)
        self._touch()
