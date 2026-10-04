# Third-party notices

The MIT License in [LICENSE](LICENSE) covers this project's source code. The pattern data
listed below was created by others and is redistributed under its own terms. Files built from
it (notably `golwall/data/library.bin`) carry the same terms as the data they contain.

## Life Lexicon

- **Files:** `sources/lexicon.json`; pattern names, cells and facts in `golwall/data/library.bin`.
- **Author:** Stephen A. Silver, with contributions from many others.
- **Source:** <https://playgameoflife.com/lexicon> (originally <http://www.argentum.freeserve.co.uk/lex.htm>).
- **License:** [Creative Commons Attribution-ShareAlike 3.0 Unported](https://creativecommons.org/licenses/by-sa/3.0/).
  Derived data in this repository is shared under the same license.

## LifeWiki pattern collection

- **Files:** `sources/lifewiki-all.zip` (unmodified); patterns and metadata in
  `golwall/data/library.bin`, `golwall/data/worlds/2-oscillator-garden.rle` and
  `golwall/data/worlds/3-spaceship-parade.rle`.
- **Source:** the ConwayLife.com pattern database, <https://conwaylife.com/patterns/all.zip>,
  maintained by the [LifeWiki](https://conwaylife.com/wiki/) community.
- **Credit:** each pattern file names its discoverer(s) in its header comments; those comments
  are preserved in the archive.
- **License:** see [LifeWiki:Copyrights](https://conwaylife.com/wiki/LifeWiki:Copyrights).

## Digital clock (clockMini)

- **Files:** `sources/clockMini.rle`, `golwall/data/worlds/1-digital-clock.rle`.
- **Author:** Vladan Majerech, reducing the seven-segment clock by "dim" (2017).
- **Sources:** <https://github.com/VladanMajerech/ConwayLifeDigitalClocks> and the Code Golf Stack
  Exchange challenge
  [*Build a digital clock in Conway's Game of Life*](https://codegolf.stackexchange.com/questions/88783/build-a-digital-clock-in-conways-game-of-life)
  (Stack Exchange contributions are licensed under
  [CC BY-SA](https://stackoverflow.com/help/licensing)).

## Pattern descriptions

The short tooltip descriptions in `sources/descriptions/` were written for this project from
the facts in the sources above (checked by `tools/check_descriptions.py` to share no run of six
or more words with the source text). They are released under CC BY-SA 3.0, matching the Life
Lexicon they draw on.

## Runtime dependencies

The packaged executable bundles [NumPy](https://numpy.org/) (BSD-3-Clause) and the
[Python](https://www.python.org/) runtime (PSF License Agreement), via
[PyInstaller](https://pyinstaller.org/) (GPL-2.0 with a bootloader exception that permits
distributing the built executable under any license).
