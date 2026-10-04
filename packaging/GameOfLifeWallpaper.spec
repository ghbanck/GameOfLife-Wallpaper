# -*- mode: python ; coding: utf-8 -*-
#
# Build with:  venv\Scripts\python -m PyInstaller packaging\GameOfLifeWallpaper.spec --clean
# from the repository root (scripts\build.py does exactly that, plus the checks around it).
#
# NumPy ships its own PyInstaller hook, which collects its DLLs and leaves its
# test suites out; a project-level numpy hook would override it and drag every
# test module into the executable, so there deliberately is none here.

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent             # this file lives in packaging/
sys.path.insert(0, str(ROOT))
from golwall import __version__                     # noqa: E402
from golwall.ui.icon import write_ico               # noqa: E402

build_dir = ROOT / "build"
build_dir.mkdir(exist_ok=True)
icon_file = build_dir / "golwall.ico"
write_ico(icon_file)

from PyInstaller.utils.win32.versioninfo import (  # noqa: E402
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo)

numbers = tuple(int(part) for part in (__version__.split(".") + ["0", "0", "0"])[:4])
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=numbers, prodvers=numbers, mask=0x3F, flags=0x0, OS=0x40004,
                      fileType=0x1, subtype=0x0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("FileDescription", "Game of Life live wallpaper"),
            StringStruct("ProductName", "Game of Life Wallpaper"),
            StringStruct("FileVersion", __version__),
            StringStruct("ProductVersion", __version__),
            StringStruct("OriginalFilename", "GameOfLifeWallpaper.exe"),
            StringStruct("InternalName", "golwall"),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)

a = Analysis(
    [str(ROOT / 'main.py')],
    pathex=[str(ROOT)],
    binaries=[],
    # The pattern library and the example worlds, read next to the code.
    datas=[(str(ROOT / 'golwall/data/library.bin'), 'golwall/data'),
           (str(ROOT / 'golwall/data/worlds/*.rle'), 'golwall/data/worlds')],
    # Imported inside functions, which the analysis also follows -- listed
    # anyway so a refactor can never silently drop one from the build.
    hiddenimports=[
        'golwall.app',
        'golwall.log',
        'golwall.capabilities.host',
        'golwall.capabilities.input',
        'golwall.capabilities.persistence',
        'golwall.capabilities.power',
        'golwall.capabilities.worlds',
        'golwall.core.camera',
        'golwall.core.grid',
        'golwall.core.library',
        'golwall.core.life',
        'golwall.core.look',
        'golwall.core.palettes',
        'golwall.core.patterns',
        'golwall.core.rle',
        'golwall.native.com',
        'golwall.native.d3d',
        'golwall.native.dcomp',
        'golwall.native.win32',
        'golwall.native.window',
        'golwall.render.renderer',
        'golwall.render.shaders',
        'golwall.ui.editor',
        'golwall.ui.icon',
        'golwall.ui.library_view',
        'golwall.ui.panel',
        'golwall.ui.text',
        'golwall.ui.theme',
        'golwall.ui.tray',
        'tkinter',
        'tkinter.filedialog',
        'tkinter.messagebox',
        'tkinter.simpledialog',
        'tkinter.font',
        'tkinter.ttk',
        'winreg',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The control panel needs tkinter; nothing here needs any of these.
    excludes=['PIL', 'matplotlib', 'pytest', 'pandas', 'scipy', 'setuptools', 'pydoc_data',
              'unittest', 'xmlrpc', 'numpy.f2py', 'numpy.distutils', 'win32com', 'pythoncom',
              'pywintypes', 'win32api'],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='GameOfLifeWallpaper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX-packed executables are a classic antivirus false positive, and it
    # can corrupt the Python and Tcl DLLs; a few extra megabytes are cheaper.
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_file),
    version=version_info,
)
