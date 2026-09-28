"""Logging that works in a packaged build with no console -- and catches crashes.

The log file rotates instead of being overwritten on every start, so the run
that crashed is still there to read afterwards.  ``faulthandler`` writes the
native stack of every thread to ``golwall.crash.log`` if the interpreter ever
dies hard (an access violation in a driver, a fatal Python error), which no
``try`` block can catch.
"""

from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import os
import sys
import threading
import time
from pathlib import Path

LOGGER = logging.getLogger("golwall")
_crash_file = None


def setup(log_path: Path | None, quiet: bool = False) -> logging.Logger:
    global _crash_file
    LOGGER.handlers.clear()
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    if log_path is not None:
        try:
            handler = logging.handlers.RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=2,
                                                           encoding="utf-8", delay=True)
            handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s",
                                                   "%Y-%m-%d %H:%M:%S"))
            LOGGER.addHandler(handler)
        except OSError:
            pass
        try:
            crash_path = Path(log_path).with_name("golwall.crash.log")
            if crash_path.exists() and crash_path.stat().st_size > 256_000:
                crash_path.replace(crash_path.with_suffix(".log.1"))
            _crash_file = open(crash_path, "a", encoding="utf-8")
            _crash_file.write(f"=== golwall started {time.strftime('%Y-%m-%d %H:%M:%S')} "
                              f"(pid {os.getpid()}) ===\n")
            _crash_file.flush()
            faulthandler.enable(_crash_file, all_threads=True)
        except OSError:
            _crash_file = None
    stream = sys.stdout or sys.stderr
    if stream is not None:
        console = logging.StreamHandler(stream)
        console.setFormatter(logging.Formatter("%(message)s"))
        console.setLevel(logging.WARNING if quiet else logging.INFO)
        LOGGER.addHandler(console)
    if not LOGGER.handlers:
        LOGGER.addHandler(logging.NullHandler())

    def excepthook(kind, value, tb):
        LOGGER.critical("uncaught exception", exc_info=(kind, value, tb))

    def thread_excepthook(args):
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread else "?"
        LOGGER.critical("uncaught exception in thread %s", name,
                        exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook
    return LOGGER


def shutdown() -> None:
    global _crash_file
    try:
        faulthandler.disable()
    except Exception:
        pass
    if _crash_file is not None:
        try:
            _crash_file.close()
        except OSError:
            pass
        _crash_file = None
    logging.shutdown()
