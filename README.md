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
