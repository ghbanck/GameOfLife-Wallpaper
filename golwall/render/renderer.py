"""Draws the world on the GPU and hands each frame to DWM.

One D3D11 device and one DirectComposition device serve every *surface* -- a
window of ours with a swap chain of its own, normally one per monitor.  Per
frame the CPU uploads nothing but the cells (one bit each, and only when they
changed) and a few hundred bytes of constants; the history pass and the
per-pixel colouring, grid and overlays all run in shaders.  That is the whole
reason a 4K wallpaper can animate at 30 fps for a fraction of a percent of CPU.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..core.camera import Viewport
from ..native import d3d
from ..native.com import ComError, ComPtr, release_all
from ..native.dcomp import Composition
from . import shaders


class DeviceLost(RuntimeError):
    """The GPU went away (driver update, TDR, sleep): rebuild everything."""


@dataclass
class Box:
    rect: tuple[int, int, int, int]                 # x, y, w, h in world cells
    edge: tuple[float, float, float, float]         # rgb 0..1 + strength
    fill: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


@dataclass
class FrameSpec:
    viewport: Viewport
    grid: tuple[tuple[int, float], ...] = ()        # (step, opacity) per level, up to 4
    smooth: bool = False
    frame_colour: tuple[float, float, float, float] | None = None
    frame_width: int = 3
    probe_colour: tuple[float, float, float] | None = None
    boxes: tuple[Box, ...] = ()                     # drawn under / over the ghost
    mask_rect: tuple[int, int, int, int] | None = None
    mask_colour: tuple[float, float, float, float] | None = None


@dataclass
class Surface:
    hwnd: int
    rect: tuple[int, int, int, int]                 # screen x, y, w, h
    origin: tuple[int, int]                         # top-left in camera pixels
    chain: d3d.SwapChain | None = None
    target: ComPtr = field(default_factory=ComPtr)
    visual: ComPtr = field(default_factory=ComPtr)
    shown: bool = True
    frames: int = 0

    @property
    def size(self) -> tuple[int, int]:
        return self.rect[2], self.rect[3]


class Renderer:
    def __init__(self, world_w: int, world_h: int, allow_hardware: bool = True) -> None:
        self.device = d3d.Device(allow_hardware)
        self.comp: Composition | None = None
        self.surfaces: list[Surface] = []
        self._world = (0, 0)
        self.cells_tex = self.cells_srv = ComPtr()
        self.hist_tex: list[ComPtr] = []
        self.hist_rtv: list[ComPtr] = []
        self.hist_srv: list[ComPtr] = []
        self.table_tex = self.table_srv = ComPtr()
        self.mask_tex = self.mask_srv = ComPtr()
        self._mask_shape = (0, 0)
        self.mask_version = -1
        try:
            self.comp = Composition(self.device.dxgi_device)
            dev = self.device
            self.vs = dev.vertex_shader(d3d.compile_shader(shaders.HLSL, "vs_fullscreen", "vs_4_0"))
            self.ps_history = dev.pixel_shader(d3d.compile_shader(shaders.HLSL, "ps_history", "ps_4_0"))
            self.ps_frame = dev.pixel_shader(d3d.compile_shader(shaders.HLSL, "ps_frame", "ps_4_0"))
            self.cb_history = dev.buffer(16)
            self.cb_frame = dev.buffer(256)
            self.table_tex = dev.texture(512, 1, d3d.DXGI_FORMAT_B8G8R8A8_UNORM, d3d.D3D11_BIND_SHADER_RESOURCE)
            self.table_srv = dev.srv(self.table_tex)
            self.resize_world(world_w, world_h)
        except Exception:
            self.release()
            raise
        self._hcb = np.zeros(4, np.float32)
        self._fcb = np.zeros(64, np.int32)
        self._front = 0
        self._cells_version = -1
        self._cells_generation = 0
        self._epoch = None
        self._table_version = -1
        self._pending_inc = 0.0
        self._pending_reset = True
        self._reset_age = 1.0
        self.frames = 0

    @property
    def driver(self) -> str:
        return self.device.driver

    # -- world-sized resources ----------------------------------------------
    def resize_world(self, width: int, height: int) -> None:
        if (width, height) == self._world:
            return
        dev = self.device
        release_all(self.cells_srv, self.cells_tex, *self.hist_srv, *self.hist_rtv, *self.hist_tex)
        self.cells_tex = dev.texture(width // 32, height, d3d.DXGI_FORMAT_R32_UINT,
                                     d3d.D3D11_BIND_SHADER_RESOURCE)
        self.cells_srv = dev.srv(self.cells_tex)
        bind = d3d.D3D11_BIND_SHADER_RESOURCE | d3d.D3D11_BIND_RENDER_TARGET
        self.hist_tex = [dev.texture(width, height, d3d.DXGI_FORMAT_R16G16_FLOAT, bind) for _ in range(2)]
        self.hist_rtv = [dev.rtv(t) for t in self.hist_tex]
        self.hist_srv = [dev.srv(t) for t in self.hist_tex]
        self._world = (width, height)
        self._cells_version = -1
        self._pending_reset = True

    def request_reset(self, age: float = 1.0) -> None:
        """Forget every trail and age on the next frame, starting live cells at ``age``."""
        self._pending_reset = True
        self._reset_age = float(age)

    # -- surfaces -------------------------------------------------------------
    def add_surface(self, hwnd: int, rect: tuple[int, int, int, int],
                    origin: tuple[int, int]) -> Surface:
        surface = Surface(int(hwnd), tuple(rect), tuple(origin))
        try:
            surface.chain = d3d.SwapChain(self.device, rect[2], rect[3])
            surface.target, surface.visual = self.comp.bind(hwnd, surface.chain.chain)
            self.comp.commit()
        except Exception:
            self._drop(surface)
            raise
        self.surfaces.append(surface)
        return surface

    def _drop(self, surface: Surface) -> None:
        release_all(surface.visual, surface.target)
        if surface.chain is not None:
            surface.chain.release()
        surface.chain, surface.visual, surface.target = None, ComPtr(), ComPtr()

    def remove_surface(self, surface: Surface) -> None:
        if surface in self.surfaces:
            self.surfaces.remove(surface)
        self.device.target(None, 1, 1)
        self._drop(surface)
        try:
            self.comp.commit()
        except ComError:
            pass

    def clear_surfaces(self) -> None:
        for surface in list(self.surfaces):
            self.remove_surface(surface)

    # -- uploads --------------------------------------------------------------
    def _upload_cells(self, world) -> None:
        if (world.w, world.h) != self._world:
            self.resize_world(world.w, world.h)
        if world.epoch != self._epoch:
            self._epoch = world.epoch
            self._pending_reset = True
        if world.version == self._cells_version:
            return
        bits = world.bits
        if not bits.flags.c_contiguous:
            bits = np.ascontiguousarray(bits)
        self.device.update(self.cells_tex, bits.ctypes.data, bits.shape[1] * 8)
        self._pending_inc += float(max(0, min(255, world.generation - self._cells_generation)))
        self._cells_generation = world.generation
        self._cells_version = world.version

    def _upload_table(self, look) -> None:
        if look.version != self._table_version:
            table = np.ascontiguousarray(look.table, dtype=np.uint8)
            self.device.update(self.table_tex, table.ctypes.data, 512 * 4)
            self._table_version = look.version

    def set_mask(self, cells: np.ndarray | None, version: int) -> None:
        """The ghost pattern shown under the pointer (0/1, any size)."""
        if version == self.mask_version:
            return
        self.mask_version = version
        if cells is None or not cells.size:
            return
        cells = np.ascontiguousarray(cells != 0, dtype=np.uint8)
        h, w = cells.shape
        if (w, h) != self._mask_shape:
            release_all(self.mask_srv, self.mask_tex)
            self.mask_tex = self.device.texture(w, h, d3d.DXGI_FORMAT_R8_UINT, d3d.D3D11_BIND_SHADER_RESOURCE)
            self.mask_srv = self.device.srv(self.mask_tex)
            self._mask_shape = (w, h)
        self.device.update(self.mask_tex, cells.ctypes.data, w)

    # -- drawing --------------------------------------------------------------
    def _history_pass(self, dt: float, look) -> None:
        dev, w, h = self.device, *self._world
        src, dst = self._front, 1 - self._front
        hcb = self._hcb
        hcb[0] = self._pending_inc
        hcb[1] = look.decay(dt)
        hcb.view(np.uint32)[2] = 1 if self._pending_reset else 0
        hcb[3] = self._reset_age
        dev.update(self.cb_history, hcb.ctypes.data)
        dev.shader_resources()
        dev.target(self.hist_rtv[dst], w, h)
        dev.pixel(self.ps_history, self.cb_history)
        dev.shader_resources(self.cells_srv, self.hist_srv[src])
        dev.draw_fullscreen()
        self._front = dst
        self._pending_inc = 0.0
        self._pending_reset = False
        self._reset_age = 1.0

    def _constants(self, spec: FrameSpec) -> np.ndarray:
        cb = self._fcb
        cf = cb.view(np.float32)
        cb[:] = 0
        vp = spec.viewport
        cb[2:4] = vp.offset_x, vp.offset_y
        cb[4:8] = vp.cell_x, vp.cell_y, self._world[0], self._world[1]
        cb[8] = max(1, int(vp.zoom))
        # Cells per pixel when zoomed out; the shader's block loops expect at most 16.
        cb[61] = max(1, min(16, int(getattr(vp, "shrink", 1))))
        flags = 0
        levels = spec.grid[:4]
        if levels:
            flags |= shaders.FLAG_GRID
            for k, (step, opacity) in enumerate(levels):
                cb[16 + k] = max(0, int(step))
                cf[12 + k] = max(0.0, min(1.0, float(opacity)))
        if spec.smooth:
            flags |= shaders.FLAG_SMOOTH
        if spec.frame_colour is not None:
            flags |= shaders.FLAG_FRAME
            cf[20:24] = spec.frame_colour
            cb[60] = max(1, int(spec.frame_width))
        if spec.probe_colour is not None:
            flags |= shaders.FLAG_PROBE
            cf[24:27] = spec.probe_colour
            cf[27] = 1.0
        for base, item in zip((28, 40), spec.boxes[:2]):
            cb[base:base + 4] = item.rect
            cf[base + 4:base + 8] = item.edge
            cf[base + 8:base + 12] = item.fill
        if spec.mask_rect is not None and spec.mask_colour is not None and self.mask_srv:
            cb[52:56] = spec.mask_rect
            cf[56:60] = spec.mask_colour
        cb[9] = flags
        return cb

    def render(self, world, look, spec: FrameSpec, dt: float,
               surfaces: list[Surface] | None = None) -> int:
        """Advance history by ``dt`` seconds and present to each surface.

        Returns how many surfaces were presented.  Raises :class:`DeviceLost`
        when the GPU is gone.
        """
        dev = self.device
        self._upload_cells(world)
        self._upload_table(look)
        dev.begin(self.vs)
        self._history_pass(dt, look)
        cb = self._constants(spec)
        presented = 0
        for surface in (self.surfaces if surfaces is None else surfaces):
            if not surface.shown or surface.chain is None:
                continue
            width, height = surface.size
            dev.target(surface.chain.rtv, width, height)
            dev.pixel(self.ps_frame, self.cb_frame)
            dev.shader_resources(self.hist_srv[self._front], self.table_srv, self.mask_srv)
            cb[0:2] = surface.origin
            cb[10:12] = width, height
            dev.update(self.cb_frame, cb.ctypes.data)
            dev.draw_fullscreen()
            hr = surface.chain.present()
            if hr in d3d.DEVICE_LOST:
                raise DeviceLost(f"Present failed: 0x{hr:08X} (removed reason "
                                 f"0x{dev.removed_reason():08X})")
            if hr == d3d.DXGI_ERROR_WAS_STILL_DRAWING:
                continue
            if hr & 0x80000000:
                raise ComError("Present", hr)
            surface.frames += 1
            presented += 1
        self.frames += 1
        return presented

    def snapshot(self, world, look, spec: FrameSpec, size: tuple[int, int],
                 origin: tuple[int, int] = (0, 0), dt: float = 0.0) -> np.ndarray:
        """Render one frame offscreen and read it back as (h, w, 4) BGRA -- for tests."""
        dev = self.device
        width, height = size
        target = dev.texture(width, height, d3d.DXGI_FORMAT_B8G8R8A8_UNORM,
                             d3d.D3D11_BIND_RENDER_TARGET | d3d.D3D11_BIND_SHADER_RESOURCE)
        rtv = dev.rtv(target)
        try:
            self._upload_cells(world)
            self._upload_table(look)
            dev.begin(self.vs)
            self._history_pass(dt, look)
            cb = self._constants(spec)
            dev.target(rtv, width, height)
            dev.pixel(self.ps_frame, self.cb_frame)
            dev.shader_resources(self.hist_srv[self._front], self.table_srv, self.mask_srv)
            cb[0:2] = origin
            cb[10:12] = width, height
            dev.update(self.cb_frame, cb.ctypes.data)
            dev.draw_fullscreen()
            dev.target(None, 1, 1)
            raw = dev.read_texture(target, width, height, d3d.DXGI_FORMAT_B8G8R8A8_UNORM, 4)
        finally:
            release_all(rtv, target)
        return np.frombuffer(raw, np.uint8).reshape(height, width, 4).copy()

    def healthy(self) -> bool:
        return bool(self.device.device) and self.device.removed_reason() == 0 and (
            self.comp is not None and self.comp.healthy())

    # -- teardown -------------------------------------------------------------
    def release(self) -> None:
        for surface in list(self.surfaces):
            self._drop(surface)
        self.surfaces.clear()
        release_all(*self.hist_srv, *self.hist_rtv, *self.hist_tex, self.cells_srv, self.cells_tex,
                    self.table_srv, self.table_tex, self.mask_srv, self.mask_tex,
                    getattr(self, "cb_frame", None), getattr(self, "cb_history", None),
                    getattr(self, "ps_frame", None), getattr(self, "ps_history", None),
                    getattr(self, "vs", None))
        self.hist_srv, self.hist_rtv, self.hist_tex = [], [], []
        if self.comp is not None:
            self.comp.release()
            self.comp = None
        self.device.release()
