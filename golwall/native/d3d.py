"""The slice of Direct3D 11 and DXGI the renderer uses, bound by vtable slot.

Slot numbers come from the ``...Vtbl`` structs in d3d11.h, dxgi.h and
dxgi1_2.h (Windows SDK 10.0.22621); see ``com`` for why that matters.  Shaders
are compiled at startup with the ``d3dcompiler_47.dll`` that ships with every
copy of Windows 10 and 11, so the package stays pure source.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from .com import HRESULT, ComError, ComPtr, check, release_all
from .win32 import GUID

d3d11 = ctypes.WinDLL("d3d11", use_last_error=True)

# --- enums ---------------------------------------------------------------------
D3D_DRIVER_TYPE_HARDWARE, D3D_DRIVER_TYPE_WARP = 1, 5
D3D11_SDK_VERSION = 7
D3D11_CREATE_DEVICE_BGRA_SUPPORT = 0x20
FEATURE_LEVELS = (0xB100, 0xB000, 0xA100, 0xA000)       # 11.1, 11.0, 10.1, 10.0

DXGI_FORMAT_R32_UINT = 42
DXGI_FORMAT_R16G16_FLOAT = 34
DXGI_FORMAT_R8_UINT = 62
DXGI_FORMAT_B8G8R8A8_UNORM = 87
DXGI_USAGE_RENDER_TARGET_OUTPUT = 0x20
DXGI_SCALING_STRETCH = 0
DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL = 3
# dxgi1_2.h: UNSPECIFIED 0, PREMULTIPLIED 1, STRAIGHT 2, IGNORE 3.  IGNORE tells
# DWM the surface is opaque, so it never has to blend a monitor-sized quad.
DXGI_ALPHA_MODE_IGNORE = 3
DXGI_PRESENT_DO_NOT_WAIT = 0x8

D3D11_USAGE_DEFAULT = 0
D3D11_BIND_CONSTANT_BUFFER = 0x4
D3D11_BIND_SHADER_RESOURCE = 0x8
D3D11_BIND_RENDER_TARGET = 0x20
D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST = 4

S_OK = 0
DXGI_ERROR_WAS_STILL_DRAWING = 0x887A000A
DXGI_ERROR_DEVICE_REMOVED = 0x887A0005
DXGI_ERROR_DEVICE_HUNG = 0x887A0006
DXGI_ERROR_DEVICE_RESET = 0x887A0007
DEVICE_LOST = {DXGI_ERROR_DEVICE_REMOVED, DXGI_ERROR_DEVICE_HUNG, DXGI_ERROR_DEVICE_RESET,
               0x887A0020}                              # DXGI_ERROR_DRIVER_INTERNAL_ERROR

IID_IDXGIDevice = GUID.parse("54EC77FA-1377-44E6-8C32-88FD5F44C84C")
IID_IDXGIDevice1 = GUID.parse("77DB970F-6276-48BA-BA28-070143B4392C")
IID_IDXGIFactory2 = GUID.parse("50C83A1C-E072-4C48-87B0-3630FA36A6D0")
IID_ID3D11Texture2D = GUID.parse("6F15AAF2-D208-4E89-9AB4-489535D34F9C")


class SAMPLE_DESC(ctypes.Structure):
    _fields_ = [("Count", wintypes.UINT), ("Quality", wintypes.UINT)]


class SWAP_CHAIN_DESC1(ctypes.Structure):
    _fields_ = [("Width", wintypes.UINT), ("Height", wintypes.UINT),
                ("Format", wintypes.UINT), ("Stereo", wintypes.BOOL),
                ("SampleDesc", SAMPLE_DESC), ("BufferUsage", wintypes.UINT),
                ("BufferCount", wintypes.UINT), ("Scaling", wintypes.UINT),
                ("SwapEffect", wintypes.UINT), ("AlphaMode", wintypes.UINT),
                ("Flags", wintypes.UINT)]


class TEXTURE2D_DESC(ctypes.Structure):
    _fields_ = [("Width", wintypes.UINT), ("Height", wintypes.UINT),
                ("MipLevels", wintypes.UINT), ("ArraySize", wintypes.UINT),
                ("Format", wintypes.UINT), ("SampleDesc", SAMPLE_DESC),
                ("Usage", wintypes.UINT), ("BindFlags", wintypes.UINT),
                ("CPUAccessFlags", wintypes.UINT), ("MiscFlags", wintypes.UINT)]


class BUFFER_DESC(ctypes.Structure):
    _fields_ = [("ByteWidth", wintypes.UINT), ("Usage", wintypes.UINT),
                ("BindFlags", wintypes.UINT), ("CPUAccessFlags", wintypes.UINT),
                ("MiscFlags", wintypes.UINT), ("StructureByteStride", wintypes.UINT)]


class VIEWPORT(ctypes.Structure):
    _fields_ = [("TopLeftX", ctypes.c_float), ("TopLeftY", ctypes.c_float),
                ("Width", ctypes.c_float), ("Height", ctypes.c_float),
                ("MinDepth", ctypes.c_float), ("MaxDepth", ctypes.c_float)]


class MAPPED_SUBRESOURCE(ctypes.Structure):
    _fields_ = [("pData", ctypes.c_void_p), ("RowPitch", wintypes.UINT),
                ("DepthPitch", wintypes.UINT)]


P = ctypes.POINTER
VOIDPP = P(ctypes.c_void_p)
PTR_ARRAY = ctypes.c_void_p * 8                           # up to eight bound views


def _compiler():
    for name in ("d3dcompiler_47", "d3dcompiler_46", "d3dcompiler_43"):
        try:
            dll = ctypes.WinDLL(name)
        except OSError:
            continue
        fn = dll.D3DCompile
        fn.restype = HRESULT
        fn.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, wintypes.UINT,
                       wintypes.UINT, VOIDPP, VOIDPP)
        return fn
    raise OSError("no HLSL compiler (d3dcompiler_47.dll) is available")


def _blob_bytes(blob: ComPtr) -> bytes:
    ptr = blob.method(3, ctypes.c_void_p)(blob.ptr)                # GetBufferPointer
    size = blob.method(4, ctypes.c_size_t)(blob.ptr)               # GetBufferSize
    return ctypes.string_at(ptr, size)


def compile_shader(source: str, entry: str, target: str) -> bytes:
    """HLSL source -> bytecode, raising with the compiler's message on failure."""
    compile_ = _compiler()
    data = source.encode("utf-8")
    code, errors = ctypes.c_void_p(), ctypes.c_void_p()
    flags = (1 << 11) | (1 << 15)          # ENABLE_STRICTNESS | OPTIMIZATION_LEVEL3
    hr = compile_(data, len(data), b"golwall.hlsl", None, None, entry.encode(), target.encode(),
                  flags, 0, ctypes.byref(code), ctypes.byref(errors))
    code_blob, error_blob = ComPtr(code), ComPtr(errors)
    try:
        if hr < 0 or not code_blob:
            detail = _blob_bytes(error_blob).decode("utf-8", "replace") if error_blob else ""
            raise ComError(f"compiling {entry}: {detail.strip()}", hr)
        return _blob_bytes(code_blob)
    finally:
        release_all(code_blob, error_blob)


class Device:
    """A D3D11 device and its immediate context, plus the DXGI objects around it."""

    def __init__(self, allow_hardware: bool = True) -> None:
        self.device = self.context = ComPtr()
        self.dxgi_device = self.factory = ComPtr()
        self.driver = ""
        self.feature_level = 0
        create = d3d11.D3D11CreateDevice
        create.restype = HRESULT
        create.argtypes = (ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, wintypes.UINT,
                           P(wintypes.UINT), wintypes.UINT, wintypes.UINT, VOIDPP,
                           P(wintypes.UINT), VOIDPP)
        drivers = ((D3D_DRIVER_TYPE_HARDWARE, "hardware"),) if allow_hardware else ()
        drivers += ((D3D_DRIVER_TYPE_WARP, "WARP (software)"),)
        last = 0
        for driver, label in drivers:
            for levels in (FEATURE_LEVELS, FEATURE_LEVELS[1:]):    # 11.1 is unknown to old runtimes
                arr = (wintypes.UINT * len(levels))(*levels)
                dev, ctx, got = ctypes.c_void_p(), ctypes.c_void_p(), wintypes.UINT(0)
                hr = create(None, driver, None, D3D11_CREATE_DEVICE_BGRA_SUPPORT, arr, len(levels),
                            D3D11_SDK_VERSION, ctypes.byref(dev), ctypes.byref(got), ctypes.byref(ctx))
                last = hr
                if hr >= 0 and dev.value:
                    self.device, self.context = ComPtr(dev), ComPtr(ctx)
                    self.driver, self.feature_level = label, got.value
                    break
            if self.device:
                break
        if not self.device:
            raise ComError("D3D11CreateDevice", last)
        try:
            self.dxgi_device = self.device.query(IID_IDXGIDevice)
            adapter = ctypes.c_void_p()
            check(self.dxgi_device.method(7, HRESULT, VOIDPP)(self.dxgi_device.ptr, ctypes.byref(adapter)),
                  "IDXGIDevice::GetAdapter")
            adapter_ptr = ComPtr(adapter)
            try:
                factory = ctypes.c_void_p()
                check(adapter_ptr.method(6, HRESULT, ctypes.c_void_p, VOIDPP)(
                    adapter_ptr.ptr, ctypes.byref(IID_IDXGIFactory2), ctypes.byref(factory)),
                    "IDXGIObject::GetParent")
                self.factory = ComPtr(factory)
            finally:
                adapter_ptr.release()
            try:                                    # fewer queued frames: less memory, less lag
                dev1 = self.device.query(IID_IDXGIDevice1)
                dev1.method(12, HRESULT, wintypes.UINT)(dev1.ptr, 1)
                dev1.release()
            except ComError:
                pass
        except Exception:
            self.release()
            raise
        self._bind_context()

    def _bind_context(self) -> None:
        c = self.context
        self._ps_srv = c.method(8, None, wintypes.UINT, wintypes.UINT, ctypes.c_void_p)
        self._ps_shader = c.method(9, None, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT)
        self._vs_shader = c.method(11, None, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT)
        self._draw = c.method(13, None, wintypes.UINT, wintypes.UINT)
        self._ps_cb = c.method(16, None, wintypes.UINT, wintypes.UINT, ctypes.c_void_p)
        self._ia_layout = c.method(17, None, ctypes.c_void_p)
        self._ia_topology = c.method(24, None, wintypes.UINT)
        self._om_targets = c.method(33, None, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p)
        self._rs_viewports = c.method(44, None, wintypes.UINT, P(VIEWPORT))
        self._copy_region = c.method(46, None, ctypes.c_void_p, wintypes.UINT, wintypes.UINT,
                                     wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT,
                                     ctypes.c_void_p)
        self._update = c.method(48, None, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p,
                                ctypes.c_void_p, wintypes.UINT, wintypes.UINT)
        self._clear_rtv = c.method(50, None, ctypes.c_void_p, P(ctypes.c_float * 4))
        self._clear_state = c.method(110, None)
        self._flush = c.method(111, None)
        self._map = c.method(14, HRESULT, ctypes.c_void_p, wintypes.UINT, wintypes.UINT,
                             wintypes.UINT, P(MAPPED_SUBRESOURCE))     # resource, sub, type, flags, out
        self._unmap = c.method(15, None, ctypes.c_void_p, wintypes.UINT)
        self._views = PTR_ARRAY()
        self._one = PTR_ARRAY()
        self._vp = VIEWPORT()

    # -- resources ------------------------------------------------------------
    def texture(self, width: int, height: int, fmt: int, bind: int, usage: int = D3D11_USAGE_DEFAULT,
                cpu: int = 0) -> ComPtr:
        desc = TEXTURE2D_DESC(int(width), int(height), 1, 1, fmt, SAMPLE_DESC(1, 0), usage, bind, cpu, 0)
        out = ctypes.c_void_p()
        check(self.device.method(5, HRESULT, P(TEXTURE2D_DESC), ctypes.c_void_p, VOIDPP)(
            self.device.ptr, ctypes.byref(desc), None, ctypes.byref(out)), "CreateTexture2D")
        return ComPtr(out)

    def buffer(self, size: int, bind: int = D3D11_BIND_CONSTANT_BUFFER) -> ComPtr:
        desc = BUFFER_DESC((int(size) + 15) // 16 * 16, D3D11_USAGE_DEFAULT, bind, 0, 0, 0)
        out = ctypes.c_void_p()
        check(self.device.method(3, HRESULT, P(BUFFER_DESC), ctypes.c_void_p, VOIDPP)(
            self.device.ptr, ctypes.byref(desc), None, ctypes.byref(out)), "CreateBuffer")
        return ComPtr(out)

    def srv(self, resource: ComPtr) -> ComPtr:
        out = ctypes.c_void_p()
        check(self.device.method(7, HRESULT, ctypes.c_void_p, ctypes.c_void_p, VOIDPP)(
            self.device.ptr, resource.ptr, None, ctypes.byref(out)), "CreateShaderResourceView")
        return ComPtr(out)

    def rtv(self, resource: ComPtr) -> ComPtr:
        out = ctypes.c_void_p()
        check(self.device.method(9, HRESULT, ctypes.c_void_p, ctypes.c_void_p, VOIDPP)(
            self.device.ptr, resource.ptr, None, ctypes.byref(out)), "CreateRenderTargetView")
        return ComPtr(out)

    def vertex_shader(self, code: bytes) -> ComPtr:
        out = ctypes.c_void_p()
        check(self.device.method(12, HRESULT, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_void_p, VOIDPP)(
            self.device.ptr, code, len(code), None, ctypes.byref(out)), "CreateVertexShader")
        return ComPtr(out)

    def pixel_shader(self, code: bytes) -> ComPtr:
        out = ctypes.c_void_p()
        check(self.device.method(15, HRESULT, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_void_p, VOIDPP)(
            self.device.ptr, code, len(code), None, ctypes.byref(out)), "CreatePixelShader")
        return ComPtr(out)

    def removed_reason(self) -> int:
        if not self.device:
            return 0
        return self.device.method(39, HRESULT)(self.device.ptr) & 0xFFFFFFFF

    def swapchain_for_composition(self, width: int, height: int) -> ComPtr:
        desc = SWAP_CHAIN_DESC1()
        desc.Width, desc.Height = max(1, int(width)), max(1, int(height))
        desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM
        desc.SampleDesc = SAMPLE_DESC(1, 0)
        desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT
        desc.BufferCount = 2
        desc.Scaling = DXGI_SCALING_STRETCH
        desc.SwapEffect = DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL
        desc.AlphaMode = DXGI_ALPHA_MODE_IGNORE
        out = ctypes.c_void_p()
        check(self.factory.method(24, HRESULT, ctypes.c_void_p, P(SWAP_CHAIN_DESC1), ctypes.c_void_p, VOIDPP)(
            self.factory.ptr, self.device.ptr, ctypes.byref(desc), None, ctypes.byref(out)),
            "CreateSwapChainForComposition")
        return ComPtr(out)

    # -- the pipeline ---------------------------------------------------------
    def begin(self, vs: ComPtr) -> None:
        self._ia_topology(self.context.ptr, D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST)
        self._ia_layout(self.context.ptr, None)
        self._vs_shader(self.context.ptr, vs.ptr, None, 0)

    def target(self, rtv: ComPtr | None, width: int, height: int) -> None:
        self._one[0] = rtv.ptr if rtv else None
        self._om_targets(self.context.ptr, 1, ctypes.cast(self._one, ctypes.c_void_p), None)
        vp = self._vp
        vp.TopLeftX = vp.TopLeftY = 0.0
        vp.Width, vp.Height = float(width), float(height)
        vp.MinDepth, vp.MaxDepth = 0.0, 1.0
        self._rs_viewports(self.context.ptr, 1, ctypes.byref(vp))

    def shader_resources(self, *views) -> None:
        for i in range(8):
            v = views[i] if i < len(views) else None
            self._views[i] = v.ptr if v else None
        self._ps_srv(self.context.ptr, 0, 8, ctypes.cast(self._views, ctypes.c_void_p))

    def pixel(self, ps: ComPtr, constants: ComPtr) -> None:
        self._ps_shader(self.context.ptr, ps.ptr, None, 0)
        self._one[0] = constants.ptr
        self._ps_cb(self.context.ptr, 0, 1, ctypes.cast(self._one, ctypes.c_void_p))

    def update(self, resource: ComPtr, data_ptr: int, row_pitch: int = 0, depth_pitch: int = 0) -> None:
        self._update(self.context.ptr, resource.ptr, 0, None, data_ptr, row_pitch, depth_pitch)

    def draw_fullscreen(self) -> None:
        self._draw(self.context.ptr, 3, 0)

    def clear(self, rtv: ComPtr, rgba=(0.0, 0.0, 0.0, 1.0)) -> None:
        colour = (ctypes.c_float * 4)(*rgba)
        self._clear_rtv(self.context.ptr, rtv.ptr, ctypes.byref(colour))

    def copy_region(self, dst: ComPtr, src: ComPtr) -> None:
        self._copy_region(self.context.ptr, dst.ptr, 0, 0, 0, 0, src.ptr, 0, None)

    def read_texture(self, texture: ComPtr, width: int, height: int, fmt: int, bpp: int) -> bytes:
        """Copy a texture back to the CPU -- for tests and diagnostics only."""
        staging = self.texture(width, height, fmt, 0, usage=3, cpu=0x20000)   # STAGING, CPU_READ
        try:
            self.copy_region(staging, texture)
            mapped = MAPPED_SUBRESOURCE()
            check(self._map(self.context.ptr, staging.ptr, 0, 1, 0, ctypes.byref(mapped)), "Map")
            try:
                rows = [ctypes.string_at(mapped.pData + y * mapped.RowPitch, width * bpp)
                        for y in range(height)]
            finally:
                self._unmap(self.context.ptr, staging.ptr, 0)
            return b"".join(rows)
        finally:
            staging.release()

    def unbind(self) -> None:
        if self.context:
            self._clear_state(self.context.ptr)
            self._flush(self.context.ptr)

    def release(self) -> None:
        try:
            self.unbind()
        except OSError:
            pass
        release_all(self.factory, self.dxgi_device, self.context, self.device)
        self.factory = self.dxgi_device = self.context = self.device = ComPtr()


class SwapChain:
    """A composition swap chain and a render target view onto its back buffer."""

    def __init__(self, device: Device, width: int, height: int) -> None:
        self.device = device
        self.width, self.height = max(1, int(width)), max(1, int(height))
        self.chain = device.swapchain_for_composition(self.width, self.height)
        self.rtv = ComPtr()
        try:
            self._make_view()
            self._present = self.chain.method(8, HRESULT, wintypes.UINT, wintypes.UINT)
        except Exception:
            self.release()
            raise

    def _make_view(self) -> None:
        back = ctypes.c_void_p()
        check(self.chain.method(9, HRESULT, wintypes.UINT, ctypes.c_void_p, VOIDPP)(
            self.chain.ptr, 0, ctypes.byref(IID_ID3D11Texture2D), ctypes.byref(back)),
            "IDXGISwapChain::GetBuffer")
        texture = ComPtr(back)
        try:
            self.rtv = self.device.rtv(texture)
        finally:
            texture.release()

    def present(self) -> int:
        """Queue the frame; returns the raw HRESULT as an unsigned value."""
        return self._present(self.chain.ptr, 0, DXGI_PRESENT_DO_NOT_WAIT) & 0xFFFFFFFF

    def resize(self, width: int, height: int) -> None:
        width, height = max(1, int(width)), max(1, int(height))
        if (width, height) == (self.width, self.height):
            return
        self.device.target(None, 1, 1)
        self.rtv.release()
        check(self.chain.method(13, HRESULT, wintypes.UINT, wintypes.UINT, wintypes.UINT,
                                wintypes.UINT, wintypes.UINT)(
            self.chain.ptr, 2, width, height, DXGI_FORMAT_B8G8R8A8_UNORM, 0), "ResizeBuffers")
        self.width, self.height = width, height
        self._make_view()

    def release(self) -> None:
        release_all(self.rtv, self.chain)
        self.rtv = self.chain = ComPtr()
