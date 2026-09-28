"""Minimal ctypes bindings for the Win32 calls this wallpaper needs.

Kept deliberately explicit: every prototype used anywhere in the package is
declared here with argtypes/restype so that pointer-sized values survive on
64-bit Windows.  No decisions live here -- see ``capabilities`` for those.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

LRESULT = ctypes.c_ssize_t
LONG_PTR = ctypes.c_ssize_t
HCURSOR = wintypes.HANDLE
HBITMAP = wintypes.HANDLE
HMENU = wintypes.HANDLE
LPVOID = ctypes.c_void_p

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

# --- window styles -----------------------------------------------------------
WS_POPUP = 0x80000000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_CLIPSIBLINGS = 0x04000000
WS_CLIPCHILDREN = 0x02000000
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_EX_NOPARENTNOTIFY = 0x00000004
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOREDIRECTIONBITMAP = 0x00200000
WS_EX_NOACTIVATE = 0x08000000
GWL_STYLE, GWL_EXSTYLE = -16, -20
SW_HIDE, SW_SHOWNORMAL, SW_SHOW, SW_SHOWNA = 0, 1, 5, 8
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER = 0x0001, 0x0002, 0x0004
SWP_NOACTIVATE, SWP_SHOWWINDOW, SWP_HIDEWINDOW = 0x0010, 0x0040, 0x0080
SWP_NOOWNERZORDER, SWP_NOSENDCHANGING = 0x0200, 0x0400
HWND_TOP, HWND_BOTTOM = 0, 1
HWND_MESSAGE = -3
CS_OWNDC, CS_DBLCLKS = 0x0020, 0x0008
IDC_ARROW = 32512
GW_HWNDNEXT, GW_HWNDPREV, GW_CHILD = 2, 3, 5
GA_ROOT = 2

# --- messages ----------------------------------------------------------------
WM_NULL, WM_CREATE, WM_DESTROY, WM_SIZE = 0x0000, 0x0001, 0x0002, 0x0005
WM_ACTIVATE, WM_CLOSE, WM_QUIT = 0x0006, 0x0010, 0x0012
WM_PAINT, WM_ERASEBKGND = 0x000F, 0x0014
WM_QUERYENDSESSION, WM_ENDSESSION = 0x0011, 0x0016
WM_SETTINGCHANGE, WM_ACTIVATEAPP, WM_SETCURSOR = 0x001A, 0x001C, 0x0020
WM_MOUSEACTIVATE = 0x0021
WM_WINDOWPOSCHANGING = 0x0046
WM_CONTEXTMENU, WM_DISPLAYCHANGE = 0x007B, 0x007E
WM_NCDESTROY, WM_NCHITTEST = 0x0082, 0x0084
WM_KEYDOWN, WM_KEYUP, WM_CHAR, WM_SYSKEYDOWN = 0x0100, 0x0101, 0x0102, 0x0104
WM_COMMAND, WM_TIMER = 0x0111, 0x0113
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK = 0x0200, 0x0201, 0x0202, 0x0203
WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0204, 0x0205, 0x0207, 0x0208
WM_MOUSEWHEEL, WM_XBUTTONDOWN, WM_XBUTTONUP, WM_MOUSEHWHEEL = 0x020A, 0x020B, 0x020C, 0x020E
WM_CAPTURECHANGED = 0x0215
WM_POWERBROADCAST = 0x0218
WM_DPICHANGED = 0x02E0
WM_WTSSESSION_CHANGE = 0x02B1
WM_HOTKEY = 0x0312
WM_PRINT, WM_PRINTCLIENT = 0x0317, 0x0318
WM_USER, WM_APP = 0x0400, 0x8000
PM_NOREMOVE, PM_REMOVE = 0x0000, 0x0001
HTTRANSPARENT = -1
MA_NOACTIVATE = 3
SPI_SETWORKAREA, SPI_SETDESKWALLPAPER = 0x002F, 0x0014

# --- waiting -----------------------------------------------------------------
QS_ALLINPUT = 0x04FF
MWMO_INPUTAVAILABLE = 0x0004
WAIT_OBJECT_0, WAIT_TIMEOUT, WAIT_FAILED = 0x0, 0x102, 0xFFFFFFFF
INFINITE = 0xFFFFFFFF
CREATE_WAITABLE_TIMER_HIGH_RESOLUTION = 0x00000002
TIMER_ALL_ACCESS = 0x001F0003

# --- system metrics ----------------------------------------------------------
SM_CXSCREEN, SM_CYSCREEN = 0, 1
SM_CXSMICON, SM_CYSMICON = 49, 50
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
SM_CMONITORS = 80
SM_REMOTESESSION = 0x1000
SMTO_NORMAL, SMTO_ABORTIFHUNG = 0x0000, 0x0002

# --- GDI ---------------------------------------------------------------------
BI_RGB, DIB_RGB_COLORS = 0, 0
SRCCOPY = 0x00CC0020
RGN_DIFF = 4
NULLREGION, ERROR_REGION = 1, 0

# --- menus and the notification area ------------------------------------------
MF_STRING, MF_GRAYED, MF_CHECKED, MF_POPUP, MF_SEPARATOR = 0x0000, 0x0001, 0x0008, 0x0010, 0x0800
MF_DEFAULT = 0x1000
TPM_RIGHTBUTTON, TPM_BOTTOMALIGN, TPM_NONOTIFY, TPM_RETURNCMD = 0x0002, 0x0020, 0x0080, 0x0100
NIM_ADD, NIM_MODIFY, NIM_DELETE, NIM_SETVERSION = 0, 1, 2, 4
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO, NIF_SHOWTIP = 0x01, 0x02, 0x04, 0x10, 0x80
NIIF_INFO, NIIF_WARNING, NIIF_USER, NIIF_NOSOUND, NIIF_LARGE_ICON = 0x1, 0x2, 0x4, 0x10, 0x20
NOTIFYICON_VERSION_4 = 4
NIN_SELECT, NIN_KEYSELECT, NIN_BALLOONUSERCLICK = WM_USER + 0, WM_USER + 1, WM_USER + 5

# --- hooks -------------------------------------------------------------------
WH_MOUSE_LL = 14
HC_ACTION = 0
LLMHF_INJECTED = 0x00000001
WINEVENT_OUTOFCONTEXT, WINEVENT_SKIPOWNPROCESS = 0x0000, 0x0002
EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_SYSTEM_MOVESIZEEND = 0x000B
EVENT_SYSTEM_MINIMIZESTART, EVENT_SYSTEM_MINIMIZEEND = 0x0016, 0x0017
EVENT_OBJECT_SHOW, EVENT_OBJECT_HIDE = 0x8002, 0x8003
EVENT_OBJECT_CLOAKED, EVENT_OBJECT_UNCLOAKED = 0x8017, 0x8018
OBJID_WINDOW = 0

# --- power and sessions --------------------------------------------------------
PBT_APMSUSPEND, PBT_APMRESUMESUSPEND = 0x0004, 0x0007
PBT_APMRESUMEAUTOMATIC, PBT_POWERSETTINGCHANGE = 0x0012, 0x8013
DEVICE_NOTIFY_WINDOW_HANDLE = 0
WTS_CONSOLE_CONNECT, WTS_CONSOLE_DISCONNECT = 1, 2
WTS_REMOTE_CONNECT, WTS_REMOTE_DISCONNECT = 3, 4
WTS_SESSION_LOCK, WTS_SESSION_UNLOCK = 7, 8
NOTIFY_FOR_THIS_SESSION = 0

# --- DWM ---------------------------------------------------------------------
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33

# --- shell -------------------------------------------------------------------
QUNS_BUSY = 2
QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4

# --- clipboard ---------------------------------------------------------------
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", HCURSOR),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wintypes.HDC), ("fErase", wintypes.BOOL),
                ("rcPaint", wintypes.RECT), ("fRestore", wintypes.BOOL),
                ("fIncUpdate", wintypes.BOOL), ("rgbReserved", ctypes.c_byte * 32)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text: str) -> "GUID":
        hexes = text.strip("{}").replace("-", "")
        g = cls()
        g.Data1 = int(hexes[0:8], 16)
        g.Data2 = int(hexes[8:12], 16)
        g.Data3 = int(hexes[12:16], 16)
        for i in range(8):
            g.Data4[i] = int(hexes[16 + 2 * i:18 + 2 * i], 16)
        return g

    def __eq__(self, other) -> bool:
        return isinstance(other, GUID) and bytes(self) == bytes(other)

    def __hash__(self) -> int:
        return hash(bytes(self))


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID),
                ("hBalloonIcon", wintypes.HICON)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


class WINDOWPOS(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("hwndInsertAfter", wintypes.HWND),
                ("x", ctypes.c_int), ("y", ctypes.c_int), ("cx", ctypes.c_int),
                ("cy", ctypes.c_int), ("flags", wintypes.UINT)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                ("yHotspot", wintypes.DWORD), ("hbmMask", HBITMAP), ("hbmColor", HBITMAP)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class POWERBROADCAST_SETTING(ctypes.Structure):
    _fields_ = [("PowerSetting", GUID), ("DataLength", wintypes.DWORD),
                ("Data", ctypes.c_ubyte * 1)]


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


def _fn(dll, name, restype, *argtypes):
    fn = getattr(dll, name)
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


PDWORD_PTR = ctypes.POINTER(ctypes.c_size_t)
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
WINEVENTPROC = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
                                  wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
TIMERPROC = ctypes.WINFUNCTYPE(None, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, wintypes.DWORD)
MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                     ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

# --- windows -------------------------------------------------------------------
RegisterClassExW = _fn(user32, "RegisterClassExW", wintypes.ATOM, ctypes.POINTER(WNDCLASSEXW))
CreateWindowExW = _fn(user32, "CreateWindowExW", wintypes.HWND,
                      wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                      wintypes.HWND, HMENU, wintypes.HINSTANCE, LPVOID)
DestroyWindow = _fn(user32, "DestroyWindow", wintypes.BOOL, wintypes.HWND)
DefWindowProcW = _fn(user32, "DefWindowProcW", LRESULT,
                     wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
ShowWindow = _fn(user32, "ShowWindow", wintypes.BOOL, wintypes.HWND, ctypes.c_int)
GetParent = _fn(user32, "GetParent", wintypes.HWND, wintypes.HWND)
IsWindow = _fn(user32, "IsWindow", wintypes.BOOL, wintypes.HWND)
IsWindowVisible = _fn(user32, "IsWindowVisible", wintypes.BOOL, wintypes.HWND)
IsIconic = _fn(user32, "IsIconic", wintypes.BOOL, wintypes.HWND)
IsHungAppWindow = _fn(user32, "IsHungAppWindow", wintypes.BOOL, wintypes.HWND)
SetWindowPos = _fn(user32, "SetWindowPos", wintypes.BOOL, wintypes.HWND, wintypes.HWND,
                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
GetWindowRect = _fn(user32, "GetWindowRect", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
GetClientRect = _fn(user32, "GetClientRect", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
GetForegroundWindow = _fn(user32, "GetForegroundWindow", wintypes.HWND)
SetForegroundWindow = _fn(user32, "SetForegroundWindow", wintypes.BOOL, wintypes.HWND)
GetWindow = _fn(user32, "GetWindow", wintypes.HWND, wintypes.HWND, wintypes.UINT)
GetAncestor = _fn(user32, "GetAncestor", wintypes.HWND, wintypes.HWND, wintypes.UINT)
WindowFromPoint = _fn(user32, "WindowFromPoint", wintypes.HWND, wintypes.POINT)
GetShellWindow = _fn(user32, "GetShellWindow", wintypes.HWND)
MonitorFromWindow = _fn(user32, "MonitorFromWindow", wintypes.HANDLE, wintypes.HWND, wintypes.DWORD)
GetMonitorInfoW = _fn(user32, "GetMonitorInfoW", wintypes.BOOL, wintypes.HANDLE,
                      ctypes.POINTER(MONITORINFO))
EnumDisplayMonitors = _fn(user32, "EnumDisplayMonitors", wintypes.BOOL, wintypes.HDC,
                          ctypes.c_void_p, MONITORENUMPROC, wintypes.LPARAM)
_get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
_get_long.restype, _get_long.argtypes = LONG_PTR, (wintypes.HWND, ctypes.c_int)
GetWindowLongPtrW = _get_long
_set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
_set_long.restype, _set_long.argtypes = LONG_PTR, (wintypes.HWND, ctypes.c_int, LONG_PTR)
SetWindowLongPtrW = _set_long
_GetClassNameW = _fn(user32, "GetClassNameW", ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_GetWindowTextW = _fn(user32, "GetWindowTextW", ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_GetWindowThreadProcessId = _fn(user32, "GetWindowThreadProcessId", wintypes.DWORD,
                                wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
SetLayeredWindowAttributes = _fn(user32, "SetLayeredWindowAttributes", wintypes.BOOL,
                                 wintypes.HWND, wintypes.COLORREF, ctypes.c_ubyte, wintypes.DWORD)
GetLayeredWindowAttributes = _fn(user32, "GetLayeredWindowAttributes", wintypes.BOOL, wintypes.HWND,
                                 ctypes.POINTER(wintypes.COLORREF), ctypes.POINTER(ctypes.c_ubyte),
                                 ctypes.POINTER(wintypes.DWORD))
LWA_ALPHA = 0x2

# --- message loop --------------------------------------------------------------
PeekMessageW = _fn(user32, "PeekMessageW", wintypes.BOOL, ctypes.POINTER(wintypes.MSG),
                   wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT)
GetMessageW = _fn(user32, "GetMessageW", wintypes.BOOL, ctypes.POINTER(wintypes.MSG),
                  wintypes.HWND, wintypes.UINT, wintypes.UINT)
TranslateMessage = _fn(user32, "TranslateMessage", wintypes.BOOL, ctypes.POINTER(wintypes.MSG))
DispatchMessageW = _fn(user32, "DispatchMessageW", LRESULT, ctypes.POINTER(wintypes.MSG))
PostQuitMessage = _fn(user32, "PostQuitMessage", None, ctypes.c_int)
PostMessageW = _fn(user32, "PostMessageW", wintypes.BOOL, wintypes.HWND, wintypes.UINT,
                   wintypes.WPARAM, wintypes.LPARAM)
PostThreadMessageW = _fn(user32, "PostThreadMessageW", wintypes.BOOL, wintypes.DWORD,
                         wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
SendMessageTimeoutW = _fn(user32, "SendMessageTimeoutW", LRESULT, wintypes.HWND, wintypes.UINT,
                          wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT, wintypes.UINT, PDWORD_PTR)
RegisterWindowMessageW = _fn(user32, "RegisterWindowMessageW", wintypes.UINT, wintypes.LPCWSTR)
MsgWaitForMultipleObjectsEx = _fn(user32, "MsgWaitForMultipleObjectsEx", wintypes.DWORD,
                                  wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
                                  wintypes.DWORD, wintypes.DWORD, wintypes.DWORD)
SetTimer = _fn(user32, "SetTimer", ctypes.c_size_t, wintypes.HWND, ctypes.c_size_t,
               wintypes.UINT, ctypes.c_void_p)
KillTimer = _fn(user32, "KillTimer", wintypes.BOOL, wintypes.HWND, ctypes.c_size_t)

# --- desktop discovery -----------------------------------------------------------
FindWindowW = _fn(user32, "FindWindowW", wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
FindWindowExW = _fn(user32, "FindWindowExW", wintypes.HWND, wintypes.HWND, wintypes.HWND,
                    wintypes.LPCWSTR, wintypes.LPCWSTR)
EnumWindows = _fn(user32, "EnumWindows", wintypes.BOOL, WNDENUMPROC, wintypes.LPARAM)
GetSystemMetrics = _fn(user32, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)

# --- painting ----------------------------------------------------------------------
GetDC = _fn(user32, "GetDC", wintypes.HDC, wintypes.HWND)
ReleaseDC = _fn(user32, "ReleaseDC", ctypes.c_int, wintypes.HWND, wintypes.HDC)
BeginPaint = _fn(user32, "BeginPaint", wintypes.HDC, wintypes.HWND, ctypes.POINTER(PAINTSTRUCT))
EndPaint = _fn(user32, "EndPaint", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(PAINTSTRUCT))
InvalidateRect = _fn(user32, "InvalidateRect", wintypes.BOOL, wintypes.HWND,
                     ctypes.POINTER(wintypes.RECT), wintypes.BOOL)
ValidateRect = _fn(user32, "ValidateRect", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
LoadCursorW = _fn(user32, "LoadCursorW", HCURSOR, wintypes.HINSTANCE, wintypes.LPCWSTR)
SetCursor = _fn(user32, "SetCursor", HCURSOR, HCURSOR)
CreateIconIndirect = _fn(user32, "CreateIconIndirect", wintypes.HICON, ctypes.POINTER(ICONINFO))
DestroyIcon = _fn(user32, "DestroyIcon", wintypes.BOOL, wintypes.HICON)

CreateCompatibleDC = _fn(gdi32, "CreateCompatibleDC", wintypes.HDC, wintypes.HDC)
CreateDIBSection = _fn(gdi32, "CreateDIBSection", HBITMAP, wintypes.HDC,
                       ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                       ctypes.POINTER(LPVOID), wintypes.HANDLE, wintypes.DWORD)
CreateBitmap = _fn(gdi32, "CreateBitmap", HBITMAP, ctypes.c_int, ctypes.c_int, wintypes.UINT,
                   wintypes.UINT, ctypes.c_void_p)
SelectObject = _fn(gdi32, "SelectObject", wintypes.HANDLE, wintypes.HDC, wintypes.HANDLE)
DeleteObject = _fn(gdi32, "DeleteObject", wintypes.BOOL, wintypes.HANDLE)
DeleteDC = _fn(gdi32, "DeleteDC", wintypes.BOOL, wintypes.HDC)
BitBlt = _fn(gdi32, "BitBlt", wintypes.BOOL, wintypes.HDC, ctypes.c_int, ctypes.c_int,
             ctypes.c_int, ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD)
CreateRectRgn = _fn(gdi32, "CreateRectRgn", wintypes.HANDLE, ctypes.c_int, ctypes.c_int,
                    ctypes.c_int, ctypes.c_int)
CombineRgn = _fn(gdi32, "CombineRgn", ctypes.c_int, wintypes.HANDLE, wintypes.HANDLE,
                 wintypes.HANDLE, ctypes.c_int)
SetRectRgn = _fn(gdi32, "SetRectRgn", wintypes.BOOL, wintypes.HANDLE, ctypes.c_int, ctypes.c_int,
                 ctypes.c_int, ctypes.c_int)

# --- menus, tray, shell --------------------------------------------------------------
CreatePopupMenu = _fn(user32, "CreatePopupMenu", HMENU)
AppendMenuW = _fn(user32, "AppendMenuW", wintypes.BOOL, HMENU, wintypes.UINT,
                  ctypes.c_size_t, wintypes.LPCWSTR)
DestroyMenu = _fn(user32, "DestroyMenu", wintypes.BOOL, HMENU)
SetMenuDefaultItem = _fn(user32, "SetMenuDefaultItem", wintypes.BOOL, HMENU, wintypes.UINT, wintypes.UINT)
TrackPopupMenuEx = _fn(user32, "TrackPopupMenuEx", ctypes.c_int, HMENU, wintypes.UINT,
                       ctypes.c_int, ctypes.c_int, wintypes.HWND, ctypes.c_void_p)
GetCursorPos = _fn(user32, "GetCursorPos", wintypes.BOOL, ctypes.POINTER(wintypes.POINT))
GetAsyncKeyState = _fn(user32, "GetAsyncKeyState", ctypes.c_short, ctypes.c_int)
Shell_NotifyIconW = _fn(shell32, "Shell_NotifyIconW", wintypes.BOOL, wintypes.DWORD,
                        ctypes.POINTER(NOTIFYICONDATAW))
SHQueryUserNotificationState = _fn(shell32, "SHQueryUserNotificationState",
                                   ctypes.c_long, ctypes.POINTER(ctypes.c_int))

# --- hotkeys ---------------------------------------------------------------------
RegisterHotKey = _fn(user32, "RegisterHotKey", wintypes.BOOL, wintypes.HWND,
                     ctypes.c_int, wintypes.UINT, wintypes.UINT)
UnregisterHotKey = _fn(user32, "UnregisterHotKey", wintypes.BOOL, wintypes.HWND, ctypes.c_int)

# --- hooks -----------------------------------------------------------------------
SetWindowsHookExW = _fn(user32, "SetWindowsHookExW", wintypes.HHOOK, ctypes.c_int,
                        HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
UnhookWindowsHookEx = _fn(user32, "UnhookWindowsHookEx", wintypes.BOOL, wintypes.HHOOK)
CallNextHookEx = _fn(user32, "CallNextHookEx", LRESULT, wintypes.HHOOK, ctypes.c_int,
                     wintypes.WPARAM, wintypes.LPARAM)
SetWinEventHook = _fn(user32, "SetWinEventHook", wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                      wintypes.HMODULE, WINEVENTPROC, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD)
UnhookWinEvent = _fn(user32, "UnhookWinEvent", wintypes.BOOL, wintypes.HANDLE)

# --- power and sessions ------------------------------------------------------------
RegisterPowerSettingNotification = _fn(user32, "RegisterPowerSettingNotification", wintypes.HANDLE,
                                       wintypes.HANDLE, ctypes.POINTER(GUID), wintypes.DWORD)
UnregisterPowerSettingNotification = _fn(user32, "UnregisterPowerSettingNotification",
                                         wintypes.BOOL, wintypes.HANDLE)
GetSystemPowerStatus = _fn(kernel32, "GetSystemPowerStatus", wintypes.BOOL,
                           ctypes.POINTER(SYSTEM_POWER_STATUS))
GUID_CONSOLE_DISPLAY_STATE = GUID.parse("6FE69556-704A-47A0-8F24-C28D936FDA47")
GUID_ACDC_POWER_SOURCE = GUID.parse("5D3E9A59-E9D5-4B00-A6BD-FF34FF516548")
GUID_POWER_SAVING_STATUS = GUID.parse("E00958C0-C213-4ACE-AC77-FECCED2EEEA5")

# --- kernel ------------------------------------------------------------------------
GetModuleHandleW = _fn(kernel32, "GetModuleHandleW", wintypes.HMODULE, wintypes.LPCWSTR)
GetCurrentThreadId = _fn(kernel32, "GetCurrentThreadId", wintypes.DWORD)
CloseHandle = _fn(kernel32, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
CreateMutexW = _fn(kernel32, "CreateMutexW", wintypes.HANDLE, ctypes.c_void_p,
                   wintypes.BOOL, wintypes.LPCWSTR)
CreateWaitableTimerExW = _fn(kernel32, "CreateWaitableTimerExW", wintypes.HANDLE, ctypes.c_void_p,
                             wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD)
SetWaitableTimer = _fn(kernel32, "SetWaitableTimer", wintypes.BOOL, wintypes.HANDLE,
                       ctypes.POINTER(ctypes.c_longlong), wintypes.LONG, ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.BOOL)
GlobalAlloc = _fn(kernel32, "GlobalAlloc", wintypes.HANDLE, wintypes.UINT, ctypes.c_size_t)
GlobalLock = _fn(kernel32, "GlobalLock", ctypes.c_void_p, wintypes.HANDLE)
GlobalUnlock = _fn(kernel32, "GlobalUnlock", wintypes.BOOL, wintypes.HANDLE)
GlobalFree = _fn(kernel32, "GlobalFree", wintypes.HANDLE, wintypes.HANDLE)
OpenClipboard = _fn(user32, "OpenClipboard", wintypes.BOOL, wintypes.HWND)
CloseClipboard = _fn(user32, "CloseClipboard", wintypes.BOOL)
EmptyClipboard = _fn(user32, "EmptyClipboard", wintypes.BOOL)
GetClipboardData = _fn(user32, "GetClipboardData", wintypes.HANDLE, wintypes.UINT)
SetClipboardData = _fn(user32, "SetClipboardData", wintypes.HANDLE, wintypes.UINT, wintypes.HANDLE)
ERROR_ALREADY_EXISTS = 183
ERROR_CLASS_ALREADY_EXISTS = 1410

# --- DWM -------------------------------------------------------------------------
DwmGetWindowAttribute = _fn(dwmapi, "DwmGetWindowAttribute", ctypes.c_long, wintypes.HWND,
                            wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
DwmSetWindowAttribute = _fn(dwmapi, "DwmSetWindowAttribute", ctypes.c_long, wintypes.HWND,
                            wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
DwmFlush = _fn(dwmapi, "DwmFlush", ctypes.c_long)

try:
    wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
    WTSRegisterSessionNotification = _fn(wtsapi32, "WTSRegisterSessionNotification",
                                         wintypes.BOOL, wintypes.HWND, wintypes.DWORD)
    WTSUnRegisterSessionNotification = _fn(wtsapi32, "WTSUnRegisterSessionNotification",
                                           wintypes.BOOL, wintypes.HWND)
except (OSError, AttributeError):          # pragma: no cover - always present on 10/11
    WTSRegisterSessionNotification = WTSUnRegisterSessionNotification = None


# ================================================================================
# Small conveniences
# ================================================================================
def enable_dpi_awareness() -> str:
    """Opt into per-monitor DPI so we address real pixels, not scaled ones."""
    try:
        fn = user32.SetProcessDpiAwarenessContext
        fn.restype, fn.argtypes = wintypes.BOOL, (wintypes.HANDLE,)
        if fn(wintypes.HANDLE(-4)):           # PER_MONITOR_AWARE_V2
            return "per-monitor-v2"
        if ctypes.get_last_error() == 5:      # ACCESS_DENIED: already set, e.g. by a manifest
            return "already set"
    except (AttributeError, OSError, ValueError):
        pass
    try:
        if user32.SetProcessDPIAware():
            return "system"
    except (AttributeError, OSError):
        pass
    return "none"


def signed_lo_hi(value: int) -> tuple[int, int]:
    """Split a packed LPARAM/WPARAM into signed 16-bit (x, y)."""
    x, y = value & 0xFFFF, (value >> 16) & 0xFFFF
    return x - 0x10000 if x > 0x7FFF else x, y - 0x10000 if y > 0x7FFF else y


def class_name(hwnd: int) -> str:
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    return buf.value if _GetClassNameW(hwnd, buf, 256) else ""


def window_text(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _GetWindowTextW(hwnd, buf, 256)
    return buf.value


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """(x, y, width, height), or zeros when the window is gone."""
    r = wintypes.RECT()
    if not hwnd or not GetWindowRect(hwnd, ctypes.byref(r)):
        return (0, 0, 0, 0)
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


def visible_frame(hwnd: int) -> tuple[int, int, int, int]:
    """The window's visible bounds as (left, top, right, bottom).

    ``GetWindowRect`` includes the invisible resize borders Windows 10 and 11
    put around every window; DWM knows where the visible frame really is.
    """
    r = wintypes.RECT()
    if DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r),
                             ctypes.sizeof(r)) == 0:
        return (r.left, r.top, r.right, r.bottom)
    if GetWindowRect(hwnd, ctypes.byref(r)):
        return (r.left, r.top, r.right, r.bottom)
    return (0, 0, 0, 0)


def is_cloaked(hwnd: int) -> bool:
    """True for windows DWM keeps hidden: other virtual desktops, suspended apps."""
    value = wintypes.DWORD(0)
    if DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)) != 0:
        return False
    return bool(value.value)


def thread_of(hwnd: int) -> int:
    return int(_GetWindowThreadProcessId(hwnd, None))


def process_of(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def ex_style(hwnd: int) -> int:
    return GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF


def virtual_screen() -> tuple[int, int, int, int]:
    """(x, y, width, height) of the whole desktop across every monitor."""
    return (GetSystemMetrics(SM_XVIRTUALSCREEN), GetSystemMetrics(SM_YVIRTUALSCREEN),
            GetSystemMetrics(SM_CXVIRTUALSCREEN), GetSystemMetrics(SM_CYVIRTUALSCREEN))


MONITORINFOF_PRIMARY = 0x00000001


def monitors() -> list[tuple[int, int, int, int, bool]]:
    """Every attached display as (x, y, width, height, is_primary), primary first."""
    found: list[tuple[int, int, int, int, bool]] = []

    def callback(handle, _hdc, _rect, _lparam):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if GetMonitorInfoW(handle, ctypes.byref(info)):
            m = info.rcMonitor
            found.append((m.left, m.top, m.right - m.left, m.bottom - m.top,
                          bool(info.dwFlags & MONITORINFOF_PRIMARY)))
        return True

    proc = MONITORENUMPROC(callback)
    EnumDisplayMonitors(None, None, proc, 0)
    found.sort(key=lambda m: (not m[4], m[0], m[1]))
    return found


def work_area(point: tuple[int, int] | None = None) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the work area of the monitor at ``point``."""
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    fn = user32.MonitorFromPoint
    fn.restype, fn.argtypes = wintypes.HANDLE, (wintypes.POINT, wintypes.DWORD)
    handle = fn(wintypes.POINT(*(point or (0, 0))), 1)          # MONITOR_DEFAULTTOPRIMARY
    if handle and GetMonitorInfoW(handle, ctypes.byref(info)):
        r = info.rcWork
        return (r.left, r.top, r.right, r.bottom)
    return (0, 0, GetSystemMetrics(SM_CXSCREEN), GetSystemMetrics(SM_CYSCREEN))


def describe_monitors() -> str:
    parts = [f"{m[2]}x{m[3]} at ({m[0]},{m[1]})" + (" primary" if m[4] else "")
             for m in monitors()]
    return "; ".join(parts) if parts else "none detected"


def cursor_pos() -> tuple[int, int] | None:
    pt = wintypes.POINT()
    if GetCursorPos(ctypes.byref(pt)):
        return pt.x, pt.y
    return None


def root_at(x: int, y: int) -> int:
    """The top-level window under a screen point, or 0."""
    hwnd = WindowFromPoint(wintypes.POINT(int(x), int(y)))
    if not hwnd:
        return 0
    return int(GetAncestor(hwnd, GA_ROOT) or hwnd)


def power_status() -> SYSTEM_POWER_STATUS | None:
    status = SYSTEM_POWER_STATUS()
    return status if GetSystemPowerStatus(ctypes.byref(status)) else None


def set_dark_title_bar(hwnd: int, dark: bool = True) -> None:
    value = ctypes.c_int(1 if dark else 0)
    DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value),
                          ctypes.sizeof(value))


# --- clipboard -------------------------------------------------------------------
def clipboard_text(owner: int = 0) -> str | None:
    if not OpenClipboard(owner):
        return None
    try:
        handle = GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            GlobalUnlock(handle)
    finally:
        CloseClipboard()


def set_clipboard_text(text: str, owner: int = 0) -> bool:
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    handle = GlobalAlloc(GMEM_MOVEABLE, size)
    if not handle:
        return False
    ptr = GlobalLock(handle)
    if not ptr:
        GlobalFree(handle)
        return False
    ctypes.memmove(ptr, data, size)
    GlobalUnlock(handle)
    if not OpenClipboard(owner):
        GlobalFree(handle)
        return False
    try:
        EmptyClipboard()
        if not SetClipboardData(CF_UNICODETEXT, handle):
            GlobalFree(handle)
            return False
        return True                     # the clipboard owns the memory now
    finally:
        CloseClipboard()


# --- single instance -------------------------------------------------------------
_instance_handle = None


def claim_single_instance(name: str = "GolWallpaperSingleInstance") -> bool:
    """Take a named mutex; False means another copy is already running."""
    global _instance_handle
    handle = CreateMutexW(None, True, "Local\\" + name)
    if not handle:
        return True                       # cannot tell; do not block startup
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        CloseHandle(handle)
        return False
    _instance_handle = handle
    return True


# --- a precise sleep that still wakes for messages ----------------------------------
class Waiter:
    """Sleep until a deadline or until a message arrives, whichever is first.

    ``time.sleep`` would leave window messages -- and the desktop's input, since
    our windows are children of Explorer's -- waiting; a plain
    ``MsgWaitForMultipleObjects`` timeout is only as fine as the 15.6 ms system
    tick.  A high-resolution waitable timer gives both.
    """

    def __init__(self) -> None:
        self.handle = CreateWaitableTimerExW(None, None, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION,
                                             TIMER_ALL_ACCESS)
        if not self.handle:                                  # before Windows 10 1803
            self.handle = CreateWaitableTimerExW(None, None, 0, TIMER_ALL_ACCESS)
        self._handles = (wintypes.HANDLE * 1)(self.handle)
        self._due = ctypes.c_longlong(0)

    def wait(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if self.handle:
            self._due.value = -max(1, int(seconds * 10_000_000))      # relative, 100 ns
            if SetWaitableTimer(self.handle, ctypes.byref(self._due), 0, None, None, False):
                MsgWaitForMultipleObjectsEx(1, self._handles, INFINITE, QS_ALLINPUT,
                                            MWMO_INPUTAVAILABLE)
                return
        MsgWaitForMultipleObjectsEx(0, None, max(1, int(seconds * 1000)), QS_ALLINPUT,
                                    MWMO_INPUTAVAILABLE)

    def close(self) -> None:
        if self.handle:
            CloseHandle(self.handle)
            self.handle = None


# --- telling the shell a window is not a full-screen app --------------------------
ole32 = ctypes.WinDLL("ole32", use_last_error=True)
_taskbar_list = None


def _vtable_call(ptr, index, restype, *argtypes):
    vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])


def mark_not_fullscreen(hwnd: int) -> bool:
    """Tell the shell a desktop-sized window is not a full-screen application.

    Otherwise the shell reads a window covering a monitor as a game going full
    screen and strips WS_EX_TOPMOST from the taskbars.  Only the top-level
    fallback window needs this; children of the desktop are never mistaken.
    """
    global _taskbar_list
    if _taskbar_list is None:
        ole32.CoInitializeEx(None, 0x2)
        ptr = ctypes.c_void_p()
        clsid = GUID.parse("56FDF344-FD6D-11D0-958A-006097C9A090")
        iid = GUID.parse("602D4995-B13A-429B-A66E-1935E44F4317")      # ITaskbarList2
        if ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid),
                                  ctypes.byref(ptr)) != 0 or not ptr:
            return False
        if _vtable_call(ptr, 3, ctypes.c_long)(ptr) != 0:             # HrInit
            return False
        _taskbar_list = ptr
    mark = _vtable_call(_taskbar_list, 8, ctypes.c_long, wintypes.HWND, wintypes.BOOL)
    return mark(_taskbar_list, hwnd, False) == 0
