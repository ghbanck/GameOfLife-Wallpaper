"""DirectComposition: handing DWM a surface of our own for a window.

This is what makes a child of Explorer's desktop visible at all on Windows 11
24H2 and later, where Progman has no redirection surface for a GDI child to
draw into -- and it is why nothing we draw can scrub the icon view beside us.
Slots are from dcomp.h, counting MSVC's reversed order for overloads.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from .com import HRESULT, ComError, ComPtr, check, release_all
from .win32 import GUID

IID_IDCompositionDevice = GUID.parse("C37EA93A-E7AA-450D-B16F-9746CB0407F3")
VOIDPP = ctypes.POINTER(ctypes.c_void_p)


class Composition:
    def __init__(self, dxgi_device: ComPtr) -> None:
        dcomp = ctypes.WinDLL("dcomp", use_last_error=True)
        create = dcomp.DCompositionCreateDevice
        create.restype = HRESULT
        create.argtypes = (ctypes.c_void_p, ctypes.c_void_p, VOIDPP)
        out = ctypes.c_void_p()
        check(create(dxgi_device.ptr, ctypes.byref(IID_IDCompositionDevice), ctypes.byref(out)),
              "DCompositionCreateDevice")
        self.device = ComPtr(out)

    def commit(self) -> None:
        if self.device:
            check(self.device.method(3, HRESULT)(self.device.ptr), "IDCompositionDevice::Commit")

    def healthy(self) -> bool:
        """IDCompositionDevice::CheckDeviceState -- False once the device is lost."""
        if not self.device:
            return False
        valid = wintypes.BOOL(0)
        try:
            check(self.device.method(26, HRESULT, ctypes.POINTER(wintypes.BOOL))(
                self.device.ptr, ctypes.byref(valid)), "CheckDeviceState")
        except ComError:
            return False
        return bool(valid.value)

    def bind(self, hwnd: int, content: ComPtr) -> tuple[ComPtr, ComPtr]:
        """Show ``content`` (a swap chain) in ``hwnd``; returns (target, visual)."""
        target, visual = ctypes.c_void_p(), ctypes.c_void_p()
        check(self.device.method(6, HRESULT, wintypes.HWND, wintypes.BOOL, VOIDPP)(
            self.device.ptr, hwnd, True, ctypes.byref(target)), "CreateTargetForHwnd")
        t = ComPtr(target)
        try:
            check(self.device.method(7, HRESULT, VOIDPP)(self.device.ptr, ctypes.byref(visual)),
                  "CreateVisual")
            v = ComPtr(visual)
            try:
                check(v.method(15, HRESULT, ctypes.c_void_p)(v.ptr, content.ptr), "SetContent")
                check(t.method(3, HRESULT, ctypes.c_void_p)(t.ptr, v.ptr), "SetRoot")
            except Exception:
                v.release()
                raise
        except Exception:
            t.release()
            raise
        return t, v

    def release(self) -> None:
        release_all(self.device)
        self.device = ComPtr()
