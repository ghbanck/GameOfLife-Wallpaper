# Security policy

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Report them privately through
GitHub's [private vulnerability reporting](https://github.com/ghbanck/GameOfLife-Wallpaper/security/advisories/new)
instead.

Include what you found, how to reproduce it, and the version affected. You should get a first
response within a week. Once a fix is released, the advisory will be published with credit to
you, unless you prefer otherwise.

## Scope

Game of Life Wallpaper runs with the signed-in user's privileges, makes no network
connections, and writes only next to its executable (or to `%APPDATA%\golwall`) and, when
*Start with Windows* is enabled, to `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.
Of particular interest are issues in parsing pattern files (RLE, `.cells`, Life 1.06) and
saved worlds, since those may come from untrusted sources.
