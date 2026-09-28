"""Settings, what earlier runs learned, the saved world, and starting with Windows.

``config.json`` is the user's and is validated field by field: a typo becomes
a warning and a default, never a crash an hour later.  ``golwall.state.json``
is ours -- the desktop layer that worked (keyed to the Windows build, so an
update makes us measure again), where the panel was, where the camera was.
``golwall.world.npz`` is the world itself, so the wallpaper survives a
reboot.  All three live beside the program when that folder is writable and
in ``%APPDATA%\\golwall`` otherwise.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import numpy as np


def windows_build() -> int:
    try:
        return int(sys.getwindowsversion().build)
    except Exception:
        return 0


def app_dir() -> Path:
    """The folder the program lives in (the executable's, when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".golwall-write-test"
        probe.write_text("", "utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


_DATA_DIR: Path | None = None


def data_dir() -> Path:
    global _DATA_DIR
    if _DATA_DIR is None:
        local = app_dir()
        if _writable(local):
            _DATA_DIR = local
        else:
            _DATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "golwall"
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
    return _DATA_DIR


def atomic_write(path: Path, data: bytes) -> None:
    """Write via a temporary file, so a crash never leaves half a file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())         # on disk before it replaces the old one
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# -- hotkeys ----------------------------------------------------------------------
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x01, 0x02, 0x04, 0x08, 0x4000
_MODS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
         "shift": MOD_SHIFT, "win": MOD_WIN, "super": MOD_WIN}
_KEYS = {"space": 0x20, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "enter": 0x0D,
         "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
         "pause": 0x13, "scrolllock": 0x91, "numlock": 0x90,
         "pageup": 0x21, "pagedown": 0x22, "backquote": 0xC0, "tilde": 0xC0}
_KEYS.update({f"f{i}": 0x6F + i for i in range(1, 25)})


def parse_hotkey(spec: str) -> tuple[int, int]:
    """``"ctrl+alt+shift+G"`` -> (modifier bits, virtual key code)."""
    mods, vk = 0, None
    for token in (t.strip().lower() for t in str(spec).split("+") if t.strip()):
        if token in _MODS:
            mods |= _MODS[token]
        elif token in _KEYS:
            vk = _KEYS[token]
        elif len(token) == 1 and (token.isalpha() or token.isdigit()):
            vk = ord(token.upper())
        else:
            raise ValueError(f"unknown key in hotkey {spec!r}: {token!r}")
    if vk is None:
        raise ValueError(f"hotkey {spec!r} has no main key")
    return mods | MOD_NOREPEAT, vk


# -- settings ---------------------------------------------------------------------
ATTACH_MODES = ("auto", "progman", "workerw", "bottom")
# Settings earlier versions wrote that no longer exist: "sustain" switched on
# the automatic life injection, which was removed because it flooded any
# world the user had built by hand.
RETIRED = frozenset({"sustain"})

_RANGES: dict[str, tuple[float, float]] = {
    "world_width": (64, 8192), "world_height": (16, 8192), "density": (0.0, 1.0),
    "warmup_generations": (0, 100_000), "generations_per_second": (0.1, 240.0),
    "fps": (1, 240), "battery_fps": (1, 240), "zoom": (0, 64), "grid_strength": (0.0, 4.0),
    "grid_opacity": (0.0, 1.0), "cycle_minutes": (0.5, 1440.0), "trail_seconds": (0.0, 30.0),
    "age_span": (1, 255), "brush_size": (1, 64),
}


@dataclass
class Config:
    # -- the world ---------------------------------------------------------
    world_width: int = 1280          # cells (rounded up to a multiple of 64)
    world_height: int = 720
    rule: str = "B3/S23"             # Conway; try B36/S23 (HighLife)
    density: float = 0.16            # initial soup fill
    warmup_generations: int = 400    # settle a fresh soup before the first frame

    # -- timing ------------------------------------------------------------
    generations_per_second: float = 8.0
    fps: int = 30                    # the editor always uses at least 60
    battery_fps: int = 12            # cap while running on battery

    # -- the view ----------------------------------------------------------
    zoom: int = 0                    # screen pixels per cell; 0 fits the whole world
    smooth: bool = False             # blend between cells instead of hard squares
    grid: bool = True                # Blender-style multi-level cell grid
    grid_levels: tuple[int, ...] = (1, 8, 64)
    grid_strength: float = 1.0       # 0 fades it out, 2 makes it bold
    grid_opacity: float = 0.15       # the grid is always white, at this opacity

    # -- look --------------------------------------------------------------
    palette: str = "matrix"
    cycle_palettes: bool = True
    cycle_minutes: float = 20.0
    trail_seconds: float = 1.6       # how long a dead cell's ghost lingers
    age_span: int = 20               # generations from the birth to the mature colour

    # -- behaviour ---------------------------------------------------------
    pause_when_busy: bool = True     # rest while a game or full-screen app is in front
    pause_when_hidden: bool = True   # rest while windows cover every monitor's desktop
    pause_on_battery_saver: bool = True
    pause_in_remote_session: bool = True
    restore_world: bool = True       # carry the world over from the last run
    tray_icon: bool = True
    # "auto" finds the slot under the icons; "progman"/"workerw" force one of
    # the two desktop layouts; "bottom" is a bottom-most window over the icons.
    attach_mode: str = "auto"

    # -- the editor --------------------------------------------------------
    hotkey: str = "ctrl+alt+shift+G"
    hotkey_fallbacks: tuple[str, ...] = ("ctrl+alt+shift+F9", "ctrl+shift+scrolllock",
                                         "ctrl+alt+shift+pause")
    brush_size: int = 1
    editor_frame: bool = True        # outline the monitors while editing
    language: str = "auto"           # "auto" follows Windows; "en" or "pt"

    # -- where this came from (not persisted) ------------------------------
    path: str = ""

    def hotkey_candidates(self) -> list[str]:
        seen, out = set(), []
        for spec in (self.hotkey, *self.hotkey_fallbacks):
            if spec and spec not in seen:
                seen.add(spec)
                out.append(spec)
        return out

    # -- validation --------------------------------------------------------
    @classmethod
    def _coerce(cls, name: str, value, default):
        kind = type(default)
        if kind is bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)) and value in (0, 1):
                return bool(value)
            if isinstance(value, str) and value.strip().lower() in ("true", "yes", "on", "1", "false", "no", "off", "0"):
                return value.strip().lower() in ("true", "yes", "on", "1")
            raise ValueError("expected true or false")
        if name == "zoom" and not isinstance(value, (bool, str)) and 0 < float(value) < 1:
            return float(value)          # zoomed out to 1/k; the camera snaps it to the nearest level
        if kind is int:
            if isinstance(value, bool):
                raise ValueError("expected a whole number")
            number = float(value)
            if not number.is_integer():
                raise ValueError("expected a whole number")
            value = int(number)
        elif kind is float:
            if isinstance(value, bool):
                raise ValueError("expected a number")
            value = float(value)
            if value != value or value in (float("inf"), float("-inf")):
                raise ValueError("expected a finite number")
        elif kind is str:
            if not isinstance(value, str):
                raise ValueError("expected text")
            value = value.strip()
        elif kind is tuple:
            if isinstance(value, str) or not isinstance(value, (list, tuple)):
                raise ValueError("expected a list")
            item_kind = type(default[0]) if default else str
            value = tuple(item_kind(v) for v in value)
        return value

    def _validate(self, name: str, value):
        """Range-check a coerced value; returns (value, warning or None)."""
        if name in _RANGES:
            low, high = _RANGES[name]
            clamped = max(low, min(high, value))
            if isinstance(value, int) and not isinstance(value, bool):
                clamped = int(clamped)
            if clamped != value:
                return clamped, f"{name} = {value!r} is out of range; using {clamped!r}"
        if name == "attach_mode" and value not in ATTACH_MODES:
            return None, f"attach_mode must be one of {', '.join(ATTACH_MODES)}"
        if name == "language" and value not in ("auto", "en", "pt"):
            return None, "language must be auto, en or pt"
        if name == "rule":
            from ..core.life import normalise_rule
            try:
                return normalise_rule(value), None
            except ValueError as exc:
                return None, f"rule {value!r} is not valid ({exc})"
        if name == "grid_levels":
            levels = tuple(sorted({max(1, min(4096, int(v))) for v in value}))[:4]
            return (levels or (1, 8, 64)), None
        return value, None

    def apply(self, data: dict) -> list[str]:
        """Take settings from a mapping, returning a warning for everything rejected."""
        warnings: list[str] = []
        defaults = Config()
        known = {f.name for f in fields(Config)} - {"path"}
        for key, raw in data.items():
            if key in RETIRED:
                continue                      # a setting an older version wrote; drop it quietly
            if key not in known:
                warnings.append(f"ignoring unknown setting {key!r}")
                continue
            default = getattr(defaults, key)
            try:
                value = self._coerce(key, raw, default)
            except (TypeError, ValueError) as exc:
                warnings.append(f"{key} = {raw!r}: {exc}; using {default!r}")
                continue
            value, warning = self._validate(key, value)
            if warning:
                warnings.append(warning)
            if value is not None:
                setattr(self, key, value)
        return warnings

    # -- files -------------------------------------------------------------
    @staticmethod
    def default_path() -> Path:
        return data_dir() / "config.json"

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> tuple["Config", list[str]]:
        """Return (config, warnings).  A missing file is not an error."""
        target = Path(path) if path else cls.default_path()
        cfg = cls()
        cfg.path = str(target)
        if not target.exists():
            return cfg, []
        try:
            data = json.loads(target.read_text("utf-8-sig"))
        except (OSError, ValueError) as exc:
            return cfg, [f"could not read {target}: {exc}; using the defaults"]
        if not isinstance(data, dict):
            return cfg, [f"{target} does not hold a settings object; using the defaults"]
        return cfg, cfg.apply(data)

    def to_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v)
                for k, v in asdict(self).items() if k != "path"}

    def save(self, path: str | os.PathLike | None = None) -> Path:
        target = Path(path) if path else Path(self.path or self.default_path())
        atomic_write(target, (json.dumps(self.to_dict(), indent=2) + "\n").encode("utf-8"))
        self.path = str(target)
        return target


# -- what earlier runs learned -------------------------------------------------------
def state_path() -> Path:
    return data_dir() / "golwall.state.json"


def load_state() -> dict:
    try:
        data = json.loads(state_path().read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(**values) -> None:
    state = load_state()
    state.update(values)
    try:
        atomic_write(state_path(), (json.dumps(state, indent=2) + "\n").encode("utf-8"))
    except OSError:
        pass


def learned_layer() -> str | None:
    """The desktop layer a previous run proved visible on this Windows build."""
    state = load_state()
    if state.get("layer_build") == windows_build() and state.get("layer") in ATTACH_MODES:
        return state["layer"]
    return None


def remember_layer(kind: str) -> None:
    save_state(layer=kind, layer_build=windows_build())


# -- the world ----------------------------------------------------------------------
def world_path() -> Path:
    return data_dir() / "golwall.world.npz"


def save_world(world, camera_state: dict | None = None) -> bool:
    import io
    buffer = io.BytesIO()
    np.savez_compressed(buffer, bits=world.bits, width=world.w, height=world.h,
                        generation=world.generation, rule=np.array(world.rule),
                        camera=np.array(json.dumps(camera_state or {})))
    try:
        atomic_write(world_path(), buffer.getvalue())
        return True
    except OSError:
        return False


def load_world(world, log=None, config_size: tuple[int, int] | None = None) -> dict | None:
    """Restore the saved world; returns its camera state, or None.

    A world of the configured size always comes back.  One of another size --
    a world opened from a file, the digital clock say -- comes back at its
    own size too, as long as config.json still asks for the size it asked
    for when that world was saved (``config_size``); a new size in the config
    means the user wants a new world.

    Whatever is wrong with the file -- empty, truncated by a power cut, from
    another version -- the answer is a fresh world, never a program that
    cannot start.  A file that cannot be read is moved aside for inspection.
    """
    path = world_path()
    if not path.exists():
        return None
    try:
        with np.load(path, allow_pickle=False) as data:
            width, height = int(data["width"]), int(data["height"])
            try:
                camera = json.loads(str(data["camera"]))
            except (KeyError, ValueError):
                camera = {}
            if not isinstance(camera, dict):
                camera = {}
            if (width, height) != (world.w, world.h):
                saved = camera.get("config_size")
                if config_size is None or not isinstance(saved, list) or tuple(saved) != tuple(config_size):
                    return None
                if not (64 <= width <= 8192 and 16 <= height <= 8192):
                    return None
            bits = data["bits"]
            if bits.shape != (height, width // 64) or bits.dtype != world.bits.dtype:
                return None
            snapshot = (bits.copy(), int(data["generation"]), str(data["rule"]))
        if not world.restore(snapshot):
            return None
        return camera
    except Exception as exc:                  # EOFError, BadZipFile, bad rule, ...
        try:
            os.replace(path, path.with_suffix(".bad.npz"))
        except OSError:
            pass
        if log is not None:
            log(f"the saved world could not be read ({type(exc).__name__}: {exc}); starting a new one")
        return None


# -- starting with Windows ------------------------------------------------------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "GameOfLifeWallpaper"


def startup_command() -> str:
    # --autostart: a launch at sign-in that finds a copy already running just
    # exits, instead of opening the editor the way a double-click does.
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}" --autostart'
    interpreter = Path(sys.executable)
    windowless = interpreter.with_name("pythonw.exe")
    if windowless.exists():
        interpreter = windowless
    return f'"{interpreter}" "{app_dir() / "main.py"}" --autostart'


def startup_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
            return bool(value)
    except OSError:
        return False


def set_startup(enabled: bool) -> bool:
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, startup_command())
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
