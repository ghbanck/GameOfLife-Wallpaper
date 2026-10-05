# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [2.0.1] - 2026-10-05

### Fixed
- **Black desktop after closing or hiding the wallpaper** on Windows 11 24H2. 2.0.0 hid the
  `WorkerW` under the icons, taking it for an empty black layer, but that window is what paints
  the Windows wallpaper. It is now kept on show (and shown again on start, repairing desktops
  2.0.0 left black); the live wallpaper sits above it, so nothing changes while it runs.

### Added
- English README (Portuguese moved to `docs/README.pt-BR.md`), hero banner and demo video.
- `LICENSE` (MIT), `THIRD_PARTY_NOTICES.md`, `CONTRIBUTING.md`, `SECURITY.md`,
  `CODE_OF_CONDUCT.md`, issue and pull request templates, CI workflow.
- `pyproject.toml` with project metadata and the `build` / `dev` extras.
- `tools/make_hero.py`, which renders the README banner from a real Life run.

### Changed
- Repository layout: tests moved to `tests/`, the build and dev-setup scripts to `scripts/`
  (`installation.py` → `scripts/build.py`, `installer-dev.py` → `scripts/setup_dev.py`),
  the PyInstaller spec to `packaging/`, and the community files to `.github/`.
- `config.json` is no longer tracked: it only held the defaults, and the program writes it
  on its own.

## [2.0.0] - 2026-09-28

A rewrite of 1.0, which overlapped applications, crashed and blocked clicks outside the editor.

### Fixed
- **Covered the icons:** the installer wrote `"presenter": "bottom"` into the executable's state,
  forcing a full-screen window above the icons — and `WS_EX_TRANSPARENT` without
  `WS_EX_LAYERED` does not let clicks through.
- **Crash** ("GIL released / thread state is NULL"): the Tk panel shared a thread with a message
  loop that dispatched Tk's own messages, and the mouse hook called Tk from inside its callback.
- **Could not click elsewhere:** the editor swallowed *every* click on screen, and the panel
  forced itself to the top every 250 ms.
- **System-wide mouse lag** with the editor open: the hook ran on a thread that slept up to
  50 ms per round.
- Monitor changes ignored, Explorer restarts falling back to the icon-covering window, no
  support for the classic Windows 10 layout, log overwritten on every run, unvalidated
  `config.json`, README instructions that did not work.

### Changed
- **Performance:** CPU pixel compositing (~20% of a core at 15 fps in 4K) replaced by
  Direct3D 11 + DirectComposition (~2–3% at 30 fps); simulation 37× faster; ~360 MB that
  NumPy's OpenBLAS reserved for unused threads released; the surface no longer declared
  translucent (wrong `DXGI_ALPHA_MODE`), which forced DWM to blend it.

### Removed
- **`sustain`** ("keep the world alive"), which treated any sparse world as dying and injected
  methuselahs, guns and whole strips of soup — a glider gun on a clean world became 25,000
  cells of chaos within 200 generations. Old configs containing `"sustain"` are accepted and
  the key is ignored.

[Unreleased]: https://github.com/ghbanck/GameOfLife-Wallpaper/compare/v2.0.1...HEAD
[2.0.1]: https://github.com/ghbanck/GameOfLife-Wallpaper/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/ghbanck/GameOfLife-Wallpaper/releases/tag/v2.0.0
