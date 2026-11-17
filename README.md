# SMB1 optimisation

Analysis and lossless size optimisation of `Super Mario Bros. (JU) (PRG0) [!].nes`
(mapper 0, 32 KiB PRG + 8 KiB CHR, crc32 `3337EC46`).

**Result: 40976 → 29526 bytes (27.9% smaller), restoring byte-for-byte.**

A 99% reduction is not achievable for this file — see [FINDINGS.md](FINDINGS.md)
for the measurements, the entropy floor, and the arithmetic showing why.

## Download

Everything you need is committed in [`dist/`](dist/):

| file | size | what it is |
|---|---:|---|
| [`super-mario-bros.nes`](dist/super-mario-bros.nes) | 40976 B | the ROM — runs in any NES emulator |
| [`super-mario-bros.smbz`](dist/super-mario-bros.smbz) | 29526 B | the packed form, 27.9% smaller |
| [`smb-optimized.zip`](dist/smb-optimized.zip) | 31459 B | **compressed and loads directly** in Mesen/FCEUX/RetroArch |
| [`smbz2nes.py`](dist/smbz2nes.py) | 12 KB | the `.smbz` → `.nes` converter, stdlib only |
| [`unpack.py`](dist/unpack.py) | 2.9 KB | minimal extractor, same format |
| [`SHA256SUMS`](dist/SHA256SUMS) | — | checksums for both |

Just want to play? Download `super-mario-bros.nes` and open it in any emulator
(Mesen, FCEUX, RetroArch, Nestopia). It is byte-identical to the original ROM.

Want the small file? Download `super-mario-bros.smbz` **and** `smbz2nes.py`
together, then:

```bash
python3 smbz2nes.py convert super-mario-bros.smbz
# -> super-mario-bros.nes, 40976 bytes, crc32 3337EC46
```

That verifies a CRC32 per stream and over the whole file, and reproduces the ROM
exactly. No dependencies beyond Python 3.

## `smbz2nes.py` — the converter

```
smbz2nes.py convert FILE.smbz [-o OUT.nes | -o DIR/] [-f] [-q] [--stdout]
smbz2nes.py verify  FILE.smbz...      # integrity only, writes nothing
smbz2nes.py info    FILE.smbz         # what is inside the container
```

| behaviour | detail |
|---|---|
| default output | same basename as the input, `.nes` |
| batch | `convert *.smbz -o roms/` — the directory is created if needed |
| overwrite | refused unless `--force`; an existing file is never clobbered |
| piping | `--stdout` writes the ROM to stdout |
| corruption | truncated / corrupt / wrong-magic files fail with a specific message and **no output file is written** |
| exit codes | `0` ok, `1` conversion or integrity failure, `2` usage error |

Checksums (also in `dist/SHA256SUMS`):

```
d2516ddc7da5bd9811407ca65bdf37e2ecee47679f2f2ad432ad88e1ebaea008  super-mario-bros.smbz
f61548fdf1670cffefcc4f0b7bdcdd9eaba0c226e3b74f8666071496988248de  super-mario-bros.nes
```

Rebuild the artifacts from the source ROM at any time with `npm run dist`.

### Why can the .nes not stay compressed?

It cannot, and the reason is the hardware rather than the file format. Running
compressed code requires decompressing it into writable memory first, and an
NROM cartridge — which SMB1 is — has only 2048 bytes of writable RAM total, for
a 32768-byte PRG image. There is 16× too little space, and the decompressor
itself would still have to be raw code at `$8000`.

The practical answers are the two above: the **zip** (23.2% smaller, opens
directly in most emulators) or the **`.smbz`** (27.9% smaller, needs the
converter). Full reasoning in
[FINDINGS.md](FINDINGS.md#appendix-can-the-nes-itself-stay-compressed).

## Decompiled source build

The game has been decompiled: `tools/build-from-source.py` assembles it from
6502 ca65 source and verifies the result is **bit-for-bit the original ROM**.

```bash
python3 tools/build-from-source.py --report
```

```
rebuilt : 40976 bytes  sha1 ea343f4e445a9050d4b4fbac2c77d0693b1d0922
original: 40976 bytes  sha1 ea343f4e445a9050d4b4fbac2c77d0693b1d0922
PASS  rebuilt ROM is BYTE-IDENTICAL to the original
```

It builds ca65/ld65 from cc65 on first run, fetches the pinned source, extracts
assets from your ROM, and assembles. Nothing copyrighted is committed: the
rebuild output and extracted assets stay in the git-ignored `build/`.

### Why it cannot be trimmed to ~23268 bytes

23268 is not a legal iNES size (they are `16 + PRG*16K + CHR*8K`); the nearest
is 24592, which needs the whole game in **one 16 KiB PRG bank**. Linking the
verified source against that config gives:

```
Segment 'PRG' overflows memory area 'ROM0' by 16384 bytes
```

Exactly double. PRG has zero padding, so the bytes must be *deleted*: all level
data, music and enemy placement together free only 6642 of the required 16384,
leaving **9742 bytes to cut from the 6502 engine itself**. There is no CHR-RAM
path either — the source has no CHR uploader. See
[FINDINGS.md](FINDINGS.md#appendix-decompilation-and-trimming-to-23268-bytes).

## World 1-1 cut

`dist/smb-1-1-only.nes` is a playable cut with **1-2 through 8-4 removed**.
Everything else survives: title screen, full sprites, Goombas and Koopas, music
and sound effects.

```bash
python3 tools/make-cut-rom.py -o dist/smb-1-1-only.nes
```

| | .nes | packed | smaller |
|---|---:|---:|---:|
| original | 40976 | 29526 | 27.9% |
| 1-1 only | **40976** | **25526** | **37.7%** |

Verified: 1-1's level and enemy streams are byte-identical to the original, the
title screen and START work, 1-1 renders with enemies, and the APU produces
audio. PRG drops 32762 → 28499 bytes.

The `.nes` is still 40976 bytes because cartridges are quantised to 16 KiB PRG
banks and 28499 is still 12115 bytes over one bank. Reaching 24592 needs a
further ~12 KB of engine surgery — see
[FINDINGS.md](FINDINGS.md#appendix-the-world-1-1-cut).

## Slim cut (levels + audio removed, game over on completion)

`dist/smb-slim.nes` removes 1-2 through 8-4 **and** all music/sound effects, and
fixes the black screen after finishing a level.

```bash
python3 tools/make-slim-rom.py --only levels,music,gameover,enemies,enemyart
```

Each cut is measured independently:

| cut | PRG saved |
|---|---:|
| `levels` — 1-2…8-4 removed | 4263 B |
| `music` — engine stubbed + streams dropped | 1610 B |
| `gameover` — completion triggers GAME OVER | 4 B |
| `enemies` — stub 14 unused handlers | 361 B |
| `enemyart` — drop 39 unused enemy sprite rows | 190 B |
| **total PRG** | **6428 B (19.6%)** |
| `chr` — blank the 231 pattern tiles nothing can draw | **−2006 B packed** |

**Finishing 1-1 triggers GAME OVER.** `handler_finish_world` used to advance to
the next world, which no longer exists and produced a garbage level. It now
enters game-over mode. Traced frame by frame: mode 2 (victory) → **mode 3 (GAME
OVER screen)** → mode 0 (title). The classic screen renders with the HUD intact
and the sequence completes on its own.

**Title-screen removal: tried, rejected.** Skipping the title also skips VRAM
initialisation, so 1-1 draws garbage metatiles (bricks fell from 3535 px to 24).
It also overflows PRG by 2 bytes. Not enabled.

**CHR blanking is the biggest single win, but it is not "removing CHR".** The
PPU addresses tiles by index, so the 8 KiB bank cannot shrink and no tile can be
renumbered. What the pass does instead is zero the 16 bytes of every tile the
game cannot draw: 231 of 512 tiles become runs of zeros and the `.nes` loses
nothing. The ROM is byte-for-byte a
different file but pixel-for-pixel the same game.

Getting that safe took several fixes, all of which used to produce a broken ROM.
The important one is the last: **a test run cannot prove a tile is unused.** The
fire flower only exists if the player is big, fire Mario needs a mushroom first,
the flag needs the end of the level, and the spiny appears in no world this cut
keeps. Any tile whose only verification was "the bot never drew it" was a
candidate for being erased, and several were.

The used set now comes from two sources that are each incomplete alone:

- **The game's own tables** — the renderer only draws an index it read from
  `tbl_player_graphics_tiles`, `tbl_enemy_graphics_tiles` (scoped to the enemies
  `tools/decode-area-enemies.py` proves world 1-1 spawns) and the scenery table.
- **A full-level sweep** — `tools/sweep-probe.js` teleports through all 16 pages
  of 1-1 and the GAME OVER screen, so nametable traffic is seen everywhere.

The other fixes:

1. **Pattern-table direction.** SMB draws the background from pattern table 1
   (`$1000`) and sprites from table 0. Mapping that backwards blanks the terrain
   and the HUD — bricks went from 3535 px to 0.
2. **The title logo is not graphics in the usual sense.** 
   `handler_copy_title_screen_from_chr` copies the raw bytes of background tiles
   236–255 through `$2007` and decodes them as the logo. Those tiles never appear
   in a nametable, so a nametable harvest calls them unused and blanking them
   deletes the logo.
3. **Harvesting is not a proof.** Sampling nametables and OAM missed tiles that
   are only visible at one scroll position (background tile 3) and a Mario sprite
   tile. The builder *blanks optimistically and then repairs*: it runs the
   blanked ROM, restores every tile that is on screen while its bytes are zero,
   and refuses to emit a ROM unless the rendered screens hash identically to the
   unblanked reference **and** every tile the data tables can point at still has
   its art. That last check is what the first version of this pass was missing.

`tools/chr-probe.js` fingerprints the title, 1-1 and the GAME OVER screen;
`tools/rom-timeline.js` is the single input sequence those checks share. The
smoke test now also fails if a tile the original ROM drew is blank.

| artifact | .nes | packed | smaller |
|---|---:|---:|---:|
| original | 40976 | 29526 | 27.9% |
| 1-1 only | 40976 | 25526 | 37.7% |
| slim, no CHR pass | 40976 | 23947 | 41.6% |
| **slim + CHR blanking** | **40976** | **21941** | **46.5%** |

Every page of 1-1 and the GAME OVER screen render identically to the same build
without the pass, and no tile referenced by the game's tables is blank.

## Play it now

```bash
npm install
npm start           # http://localhost:8000
```

The harness serves the ROM from the repo root to a jsnes-backed page, so what you
play always matches the file under test. Keyboard: arrows to move, `Z` jump,
`X` run/fire, `Enter` start. Touch controls appear on phones.

Verified two ways:

```bash
npm test            # headless: boots to title, presses START, checks World 1-1
npm run test:page   # loads the real page in jsdom, asserts frames are painted
python3 tests/test_rompack.py     # 6 tests: container round-trip
python3 tests/test_smbz2nes.py    # 21 tests: converter, incl. corruption paths
```

The page recomputes the ROM's CRC32 in the browser (`3337EC46`) as proof it read
the same bytes the analysis was done on.

> Note: jsnes ships a misaligned NTSC palette (it paints SMB1's blue sky salmon).
> The harness applies jsnes's own measured `loadDefaultPalette()` table, which
> restores the correct sky and brick colours.

## Quick start

```bash
# full size/entropy breakdown + codec comparison
python3 tools/rompack.py analyze "Super Mario Bros. (JU) (PRG0) [!].nes"

# compress (verifies the round-trip automatically)
python3 tools/rompack.py pack "Super Mario Bros. (JU) (PRG0) [!].nes" -o smb1.smbz

# restore -> byte-identical .nes
python3 tools/rompack.py unpack smb1.smbz -o restored.nes

# tests
python3 tests/test_rompack.py
```

## How it works

The ROM is split into its header, PRG and CHR sections. Each section is stored as
a **raw LZMA2 stream** with its own `lc`/`lp`/`pb` context parameters chosen by
exhaustive search, so there is no `.xz`/`.7z` container overhead — the whole
container header is ~40 bytes. `unpack` verifies a CRC32 per stream and a CRC32
over the reconstructed file, so corruption cannot pass silently.

The per-stream context tuning is what beats `xz -9e` on the same bytes:

| codec | bytes | reduction |
|---|---:|---:|
| as shipped | 40976 | — |
| zlib -9 | 31333 | 23.5% |
| brotli -q11 | 30194 | 26.3% |
| xz -9e (single stream) | 29660 | 27.6% |
| **rompack** | **29526** | **27.9%** |

## Notes on the requested per-area work

The original request also covered sprites, audio, code and levels. Each of those
is a behaviour-changing edit, and the measurements in FINDINGS.md show the
remaining slack in this ROM is under 1%: there is no padding, zero duplicate
code blocks, and only 64 bytes of duplicate tiles. Those edits would spend real
regression risk to buy fractions of a percent. The compression ceiling of ~28% is
a property of the data, not of the effort applied.

## Files

| file | purpose |
|---|---|
| `tools/rompack.py` | analyzer / packer / unpacker |
| `tests/test_rompack.py` | round-trip and corruption-detection tests |
| `FINDINGS.md` | the measurements and the ceiling argument |
| `dist/` | the downloadable ROM, packed form, extractor and checksums |
| `tools/build-dist.py` | rebuilds `dist/` and verifies the whole chain |
