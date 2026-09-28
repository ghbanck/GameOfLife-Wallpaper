"""Notification-area icon and its context menu.

A single click opens (or closes) the editor; right click gives the menu.  The
menu is modal -- ``TrackPopupMenuEx`` runs its own message loop until the user
picks something -- so the app keeps the wallpaper animating from a timer while
it is open instead of freezing behind it.
"""

from __future__ import annotations

import ctypes

from ..capabilities import worlds
from ..native import win32 as w
from . import icon as icon_module
from .text import language, t

WM_TRAY = w.WM_APP + 1
ICON_ID = 1

(CMD_EDITOR, CMD_PAUSE, CMD_SOUP, CMD_CLEAR, CMD_PALETTE, CMD_SHOW,
 CMD_STARTUP, CMD_SETTINGS, CMD_LOG, CMD_EXIT) = range(1, 11)
CMD_WORLD = 100                  # + the world's index in the list
MAX_WORLDS = 40


class Tray:
    def __init__(self, app) -> None:
        self.app = app
        self.hwnd = app.control.hwnd
        self.hicon = icon_module.make_hicon()
        self._added = False
        self._tip = ""
        self.balloon = ""                # what the last balloon was about: "hint" or "warning"
        self._worlds: list = []
        self.add()

    def _data(self, flags: int) -> w.NOTIFYICONDATAW:
        nid = w.NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(w.NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = ICON_ID
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self.hicon
        nid.szTip = self.tooltip()[:127]
        return nid

    def tooltip(self) -> str:
        app = self.app
        if not app.wallpaper_visible:
            state = t("tip.hidden")
        elif app.paused:
            state = t("tip.paused")
        elif app.rest_reason:
            state = t("tip.resting", why=app.rest_reason)
        else:
            state = t("tip.running", gps=round(app.gps, 1))
        return t("tip", state=state)

    def add(self) -> None:
        """Put the icon in the notification area, or refresh it if it is there.

        TaskbarCreated also arrives when the taskbar merely changed DPI, with
        our icon still present; NIM_ADD then fails, and treating that as "no
        icon" would silence every later update and leave it behind at exit.
        """
        nid = self._data(w.NIF_MESSAGE | w.NIF_ICON | w.NIF_TIP | w.NIF_SHOWTIP)
        added = bool(w.Shell_NotifyIconW(w.NIM_ADD, ctypes.byref(nid)))
        if not added:
            added = bool(w.Shell_NotifyIconW(w.NIM_MODIFY, ctypes.byref(nid)))
        self._added = added
        if added:
            nid.uVersion = w.NOTIFYICON_VERSION_4
            w.Shell_NotifyIconW(w.NIM_SETVERSION, ctypes.byref(nid))
            self._tip = nid.szTip

    def update(self) -> None:
        if not self._added:
            return
        tip = self.tooltip()[:127]
        if tip == self._tip:
            return
        nid = self._data(w.NIF_TIP | w.NIF_SHOWTIP)
        w.Shell_NotifyIconW(w.NIM_MODIFY, ctypes.byref(nid))
        self._tip = tip

    def notify(self, title: str, body: str, warning: bool = False) -> None:
        if not self._added:
            return
        self.balloon = "warning" if warning else "hint"
        nid = self._data(w.NIF_INFO)
        nid.szInfoTitle = title[:63]
        nid.szInfo = body[:255]
        nid.dwInfoFlags = (w.NIIF_WARNING if warning else w.NIIF_INFO) | w.NIIF_NOSOUND
        w.Shell_NotifyIconW(w.NIM_MODIFY, ctypes.byref(nid))

    def remove(self) -> None:
        if self._added:
            nid = self._data(0)
            w.Shell_NotifyIconW(w.NIM_DELETE, ctypes.byref(nid))
            self._added = False
        if self.hicon:
            w.DestroyIcon(self.hicon)
            self.hicon = None

    # -- interaction ------------------------------------------------------
    def on_callback(self, wparam: int, lparam: int) -> None:
        event = lparam & 0xFFFF
        if event in (w.NIN_SELECT, w.NIN_KEYSELECT):
            self.app.toggle_interactive()
        elif event == w.NIN_BALLOONUSERCLICK:
            # The hint invites you to draw; a warning is explained in the log.
            if self.balloon == "warning":
                self.app.open_log()
            else:
                self.app.set_interactive(True)
        elif event == w.WM_CONTEXTMENU:
            x, y = w.signed_lo_hi(wparam)
            self.show_menu(x, y)

    def show_menu(self, x: int | None = None, y: int | None = None) -> None:
        app = self.app
        menu = w.CreatePopupMenu()
        if not menu:
            return
        try:
            hint = f"\t{app.hotkey_spec}" if app.hotkey_spec else ""
            flags = w.MF_STRING | (w.MF_CHECKED if app.interactive else 0)
            w.AppendMenuW(menu, flags, CMD_EDITOR, t("menu.draw") + hint)
            w.SetMenuDefaultItem(menu, CMD_EDITOR, 0)
            w.AppendMenuW(menu, w.MF_SEPARATOR, 0, None)
            w.AppendMenuW(menu, w.MF_STRING, CMD_PAUSE, t("menu.resume") if app.paused else t("menu.pause"))
            w.AppendMenuW(menu, w.MF_STRING, CMD_SOUP, t("menu.soup"))
            w.AppendMenuW(menu, w.MF_STRING, CMD_CLEAR, t("menu.clear"))
            w.AppendMenuW(menu, w.MF_STRING, CMD_PALETTE, t("menu.palette"))
            try:
                self._worlds = worlds.list_worlds(language())[:MAX_WORLDS]
            except Exception:                      # the menu, and Exit, must always open
                self._worlds = []
            if self._worlds:
                sub = w.CreatePopupMenu()
                if sub:
                    for index, item in enumerate(self._worlds):
                        label = t("worlds.starter", name=item.name) if item.starter else item.name
                        checked = w.MF_CHECKED if item.name == app.world_name else 0
                        w.AppendMenuW(sub, w.MF_STRING | checked, CMD_WORLD + index, label.replace("&", "&&"))
                    # The submenu now belongs to the menu and is destroyed with it.
                    w.AppendMenuW(menu, w.MF_POPUP, sub, t("menu.worlds"))
            w.AppendMenuW(menu, w.MF_STRING, CMD_SHOW,
                          t("menu.show") if not app.wallpaper_visible else t("menu.hide"))
            w.AppendMenuW(menu, w.MF_SEPARATOR, 0, None)
            w.AppendMenuW(menu, w.MF_STRING | (w.MF_CHECKED if app.startup_enabled() else 0),
                          CMD_STARTUP, t("menu.startup"))
            w.AppendMenuW(menu, w.MF_STRING, CMD_SETTINGS, t("menu.settings"))
            w.AppendMenuW(menu, w.MF_STRING, CMD_LOG, t("menu.log"))
            w.AppendMenuW(menu, w.MF_SEPARATOR, 0, None)
            w.AppendMenuW(menu, w.MF_STRING, CMD_EXIT, t("menu.exit"))
            if x is None or y is None:
                x, y = w.cursor_pos() or (0, 0)
            # The menu needs a foreground window or it will not dismiss on click-away.
            w.SetForegroundWindow(self.hwnd)
            app.begin_modal()
            try:
                choice = w.TrackPopupMenuEx(menu, w.TPM_RIGHTBUTTON | w.TPM_RETURNCMD | w.TPM_NONOTIFY,
                                            x, y, self.hwnd, None)
            finally:
                app.end_modal()
            w.PostMessageW(self.hwnd, w.WM_NULL, 0, 0)
        finally:
            w.DestroyMenu(menu)
        self.on_command(choice)

    def on_command(self, command: int) -> None:
        app = self.app
        if command == CMD_EDITOR:
            app.toggle_interactive()
        elif command == CMD_PAUSE:
            app.toggle_pause()
        elif command == CMD_SOUP:
            app.reseed()
        elif command == CMD_CLEAR:
            app.clear()
        elif command == CMD_PALETTE:
            app.next_palette()
        elif command == CMD_SHOW:
            app.toggle_wallpaper_visible()
        elif command == CMD_STARTUP:
            app.set_startup(not app.startup_enabled())
        elif command == CMD_SETTINGS:
            app.open_settings()
        elif command == CMD_LOG:
            app.open_log()
        elif command == CMD_EXIT:
            app.stop()
        elif CMD_WORLD <= command < CMD_WORLD + len(self._worlds):
            item = self._worlds[command - CMD_WORLD]
            app.load_world_file(str(item.path), item.name)
            app.panel.post("worlds")
        self.update()
