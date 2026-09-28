"""The glider icon, drawn at whatever size is asked for.

Generated rather than shipped: the tray gets an ``HICON`` at the exact size
the current DPI wants, the control panel gets PNG bytes, and the build gets a
multi-size ``.ico`` -- all from the same few lines, with no binary assets to
keep in step with the code.
"""

from __future__ import annotations

import ctypes
import struct
import zlib

import numpy as np

from ..core import patterns
from ..native import win32 as w

GREEN = (62, 224, 110)


def glider_rgba(size: int, colour: tuple[int, int, int] = GREEN) -> np.ndarray:
    """A glider on a transparent square, as (size, size, 4) RGBA."""
    size = max(8, int(size))
    img = np.zeros((size, size, 4), np.uint8)
    art = patterns.get("glider")
    pad = max(1, round(size * 0.06))
    cell = (size - 2 * pad) / 3.0
    gap = 0 if size < 16 else max(1, round(cell * 0.14))
    for y, x in zip(*np.nonzero(art)):
        x0 = pad + round(x * cell) + gap // 2
        y0 = pad + round(y * cell) + gap // 2
        x1 = pad + round((x + 1) * cell) - (gap - gap // 2)
        y1 = pad + round((y + 1) * cell) - (gap - gap // 2)
        img[y0:y1, x0:x1, :3] = colour
        img[y0:y1, x0:x1, 3] = 255
    return img


def png_bytes(rgba: np.ndarray) -> bytes:
    height, width = rgba.shape[:2]
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico_bytes(sizes=(16, 20, 24, 32, 40, 48, 64, 128, 256)) -> bytes:
    images = [png_bytes(glider_rgba(s)) for s in sizes]
    out = [struct.pack("<HHH", 0, 1, len(sizes))]
    offset = 6 + 16 * len(sizes)
    for size, data in zip(sizes, images):
        out.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    return b"".join(out) + b"".join(images)


def write_ico(path) -> None:
    from pathlib import Path
    Path(path).write_bytes(ico_bytes())


def make_hicon(size: int | None = None, colour: tuple[int, int, int] = GREEN) -> int:
    """An HICON of the glider; the caller owns it (``DestroyIcon``)."""
    size = size or w.GetSystemMetrics(w.SM_CXSMICON) or 16
    rgba = glider_rgba(size, colour)
    info = w.BITMAPINFO()
    head = info.bmiHeader
    head.biSize = ctypes.sizeof(w.BITMAPINFOHEADER)
    head.biWidth, head.biHeight = size, -size
    head.biPlanes, head.biBitCount, head.biCompression = 1, 32, w.BI_RGB
    bits = w.LPVOID()
    screen = w.GetDC(None)
    colour_bmp = w.CreateDIBSection(screen, ctypes.byref(info), w.DIB_RGB_COLORS,
                                    ctypes.byref(bits), None, 0)
    w.ReleaseDC(None, screen)
    if not colour_bmp or not bits:
        return 0
    bgra = np.ascontiguousarray(rgba[..., [2, 1, 0, 3]])
    ctypes.memmove(bits, bgra.ctypes.data, bgra.nbytes)
    mask_bits = (ctypes.c_ubyte * (((size + 15) // 16) * 2 * size))()
    mask_bmp = w.CreateBitmap(size, size, 1, 1, mask_bits)
    try:
        icon = w.ICONINFO(True, 0, 0, mask_bmp, colour_bmp)
        return int(w.CreateIconIndirect(ctypes.byref(icon)) or 0)
    finally:
        w.DeleteObject(colour_bmp)
        if mask_bmp:
            w.DeleteObject(mask_bmp)
