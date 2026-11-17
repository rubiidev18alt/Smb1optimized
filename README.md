# SMB1 optimisation

Analysis and lossless size optimisation of `Super Mario Bros. (JU) (PRG0) [!].nes`
(mapper 0, 32 KiB PRG + 8 KiB CHR, crc32 `3337EC46`).

**Result: 40976 → 29526 bytes (27.9% smaller), restoring byte-for-byte.**

A 99% reduction is not achievable for this file — see [FINDINGS.md](FINDINGS.md)
for the measurements, the entropy floor, and the arithmetic showing why.

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
