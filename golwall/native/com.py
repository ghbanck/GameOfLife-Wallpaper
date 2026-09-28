"""Just enough COM to call methods by vtable slot from ctypes.

Every slot number used in ``d3d`` and ``dcomp`` was taken from the C
declarations in the Windows SDK headers (the ``...Vtbl`` structs), because a
wrong slot does not fail politely -- it calls some other method with the wrong
arguments.  Bound methods are cached per interface pointer: building a ctypes
function object costs more than the call it wraps.
"""

from __future__ import annotations

import ctypes

HRESULT = ctypes.c_long


class ComError(OSError):
    def __init__(self, what: str, hr: int) -> None:
        self.hr = hr & 0xFFFFFFFF
        super().__init__(f"{what} failed: 0x{self.hr:08X}")


def check(hr: int, what: str) -> int:
    if hr < 0:
        raise ComError(what, hr)
    return hr


class ComPtr:
    """An owned reference to one COM interface."""

    __slots__ = ("ptr", "_methods")

    def __init__(self, ptr=0) -> None:
        if isinstance(ptr, ctypes.c_void_p):
            ptr = ptr.value
        self.ptr = int(ptr or 0)
        self._methods: dict[int, object] = {}

    def __bool__(self) -> bool:
        return bool(self.ptr)

    def method(self, index: int, restype, *argtypes):
        fn = self._methods.get(index)
        if fn is None:
            if not self.ptr:
                raise ComError(f"call on a released interface (slot {index})", -1)
            vtable = ctypes.cast(ctypes.c_void_p(self.ptr),
                                 ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
            fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])
            self._methods[index] = fn
        return fn

    def query(self, iid) -> "ComPtr":
        out = ctypes.c_void_p()
        hr = self.method(0, HRESULT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
            self.ptr, ctypes.byref(iid), ctypes.byref(out))
        check(hr, "QueryInterface")
        return ComPtr(out)

    def release(self) -> None:
        if self.ptr:
            try:
                self.method(2, ctypes.c_ulong)(self.ptr)
            finally:
                self.ptr = 0
                self._methods.clear()


def release_all(*items) -> None:
    for item in items:
        if item:
            try:
                item.release()
            except OSError:
                pass
