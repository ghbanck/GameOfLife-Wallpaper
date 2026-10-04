# Contributing

Thanks for your interest in Game of Life Wallpaper! Bug reports, ideas and pull requests are
all welcome. Issues and pull requests may be written in English or Portuguese.

## Reporting bugs

Open a [bug report](https://github.com/ghbanck/GameOfLife-Wallpaper/issues/new/choose) and
include:

- Windows version and build (`winver`), GPU and monitor setup;
- the output of `GameOfLifeWallpaper.exe --diagnose`;
- the relevant part of `golwall.log` (and `golwall.crash.log`, if there is one).

Logs can contain folder paths from your machine — glance over them before posting.

## Development setup

```powershell
git clone https://github.com/ghbanck/GameOfLife-Wallpaper.git
cd GameOfLife-Wallpaper
python installer-dev.py              # venv + numpy + tests
venv\Scripts\python main.py -w       # run in a window, logging to the terminal
```

`python installation.py` builds the executable. See the
[Development section of the README](README.md#development) for the project layout.

## Pull requests

1. Fork, then create a branch from `main`.
2. Keep each pull request focused on one change.
3. Run the full suite on Windows before opening the PR — it includes GPU checks that CI
   cannot run:

   ```powershell
   venv\Scripts\python tests.py
   ```

4. If you change `sources/`, rebuild the library (`tools\build_library.py`) and check the
   example worlds (`tools\make_worlds.py --check`).
5. If the change is user-visible, add a line under **Unreleased** in
   [CHANGELOG.md](CHANGELOG.md), and update both READMEs when it affects documented behaviour.
6. New UI text needs both an English and a Portuguese string in `golwall/ui/text.py`.

## Code style

- Match the surrounding code: type hints, `from __future__ import annotations`, comments that
  explain *why* rather than *what*.
- Standard library + NumPy only at runtime. Native APIs go through `ctypes` in `golwall/native/`.
- Configuration must never crash the program: invalid values become a log warning and the default.

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By participating you
agree to uphold it.
