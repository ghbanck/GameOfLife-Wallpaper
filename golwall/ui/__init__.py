"""Everything the user touches: the tray icon, the editor, the control panel.

Each piece talks to the application through its command surface -- pause,
skip, clear, stamp, select -- and none of them reaches into the renderer or
the desktop directly.  The panel runs on a thread of its own and only ever
talks to the app through ``app.call`` and ``app.snapshot``.
"""
