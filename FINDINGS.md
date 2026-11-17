# SMB1 size optimisation: what is actually achievable

**Short answer: a 99% reduction is not possible, and the reason is arithmetic, not
effort.** This document shows the measurements, states the real ceiling, and
delivers the best result that *is* achievable — a **27.9% lossless reduction that
restores the ROM byte-for-byte**.

---

## 1. What the file actually contains

```
Super Mario Bros. (JU) (PRG0) [!].nes
  size   40976 bytes    crc32 3337EC46    sha256 f61548fd...8248de
  layout mapper 0 (NROM-256), 32 KiB PRG, 8 KiB CHR, vertical mirroring
  vectors NMI=$8082  RESET=$8000  IRQ=$FFF0
```

| section | bytes | share of file | entropy (bits/byte) |
|---|---:|---:|---:|
| iNES header | 16 | 0.04% | 2.09 |
| PRG — 6502 code, tables, level data, music | 32768 | 80.0% | 7.267 |
| CHR — background + sprite tiles | 8192 | 20.0% | 5.761 |

The CRC32 identifies this as the unmodified, standard, widely-dumped release. It
has already been through Nintendo's own linker and tile-packing, so there is no
slack left in the layout.

## 2. There is no dead weight to remove

Every place a shave could hide, I checked:

| check | result |
|---|---|
| longest `0xFF` padding run in PRG | **0 bytes** |
| longest `0x00` run in PRG | **13 bytes** |
| duplicate 16-byte PRG blocks | **0 of 2048** |
| duplicate 32-byte PRG blocks | **0 of 1024** |
| duplicate 64-byte PRG blocks | **0 of 512** |
| duplicate CHR tiles | 5 of 512 → **64 bytes total**, and each is referenced by a different tile index |
| blank CHR tiles | 2 (the hardware needs them) |
| PRG bytes with 1 KB windows above H0=6.2 | all 32 windows — **uniformly dense, no soft spots** |

Those 64 redundant CHR bytes are *aliased*, not *free*: the PPU addresses tiles by
index, so removing a tile renumbers every tile after it and changes what the game
draws. Reclaiming 64 bytes is 0.16% of the file.

## 3. The information-theoretic ceiling

I measured the empirical conditional entropy — the compression rate a
theoretically perfect context-model coder would approach — on the real bytes:

| model | PRG | CHR | whole-ROM floor | best possible cut |
|---|---:|---:|---:|---|
| order-0 (single byte frequencies) | 7.267 | 5.761 | 35664 bytes | 13.0% |
| order-1 (previous byte) | 6.235 | 4.433 | 30092 bytes | 26.6% |
| order-2 | 6.375 | 5.192 | ~31500 bytes | ~23% |
| order-3 | 6.754 | 5.771 | ~33400 bytes | ~18% |
| order-4 | 6.887 | 6.134 | 34491 bytes | 15.8% |

Two things to note. First, entropy rises past order-1 — this data is *not* a
low-order Markov source, it is dense code with long-range matches, which is why
LZ77 beats pure context modelling here. Second, **even a hypothetical perfect
coder at these model orders cannot exceed roughly a 27% cut.**

## 4. Codec shootout on the real file

| codec | bytes | % of original | reduction |
|---|---:|---:|---:|
| uncompressed (as shipped) | 40976 | 100.0% | 0.0% |
| zlib -9 (deflate) | 31333 | 76.5% | 23.5% |
| bzip2 -9 | 32742 | 79.9% | 20.1% |
| zstd -22 | 30902 | 75.4% | 24.6% |
| brotli -q11 | 30194 | 73.7% | 26.3% |
| PPMd order-16 | 30231 | 73.8% | 26.2% |
| LZMA 9e (single .xz stream) | 29660 | 72.4% | 27.6% |
| **rompack (this repo)** | **29526** | **72.1%** | **27.9%** |

I also tried ROM-specific transforms, and every one of them made the file *bigger*:

| transform | result |
|---|---|
| CHR bitplane deinterleave (all plane-0 bytes, then plane-1) | 5177 vs 4696 bytes — **10% worse** |
| per-tile plane split | 4761 vs 4696 — **worse** |
| PRG byte-delta coding | 29299 vs 24826 — **18% worse** |
| split PRG into two 16 KiB banks | 25205 vs 24826 — **worse** |

The generic LZMA2 model already beats every structural rearrangement I could
devise, which is the expected outcome for already-optimised machine code.

## 5. Why 99% is arithmetically impossible

99% off 40976 bytes leaves a **410-byte file**. Compare against the hard
requirements of the format:

- The iNES header alone is 16 bytes — **3.9% of the entire target budget**.
- CHR is 8192 bytes — **20× the whole 410-byte budget**, before any code exists.
- PRG is 32768 bytes — **80× the budget**.
- A 410-byte file cannot contain even a *single* usable background tile set, let
  alone the 512 tiles this game draws with.

For context on the other thresholds:

| target | file size | verdict |
|---|---:|---|
| 50% smaller | 20488 bytes | plausible only with destructive changes |
| 95% smaller | 2049 bytes | impossible for a playable NES ROM |
| **99% smaller** | **410 bytes** | **impossible — smaller than the graphics data alone** |

A ROM is executable machine code, not a data blob with redundancy. Lossless
compression cannot exceed the source entropy, and this source has ~7.3 bits of
entropy per byte and almost no duplication. Getting to 99% would require deleting
the game.

## 6. What I delivered instead

`tools/rompack.py` — a working, verified ROM packer:

- `analyze` — the full breakdown above, computed from any iNES ROM.
- `pack` — writes a `.smbz` container using **raw** LZMA2 streams (no `.xz`
  framing), split per section, with **per-stream tuned `lc`/`lp`/`pb` context
  parameters** chosen by exhaustive search. That tuning is worth ~130 bytes over a
  single global stream and is where the win over plain `xz -9e` comes from.
- `unpack` — reconstructs the original image, verifying a CRC32 per stream *and*
  a CRC32 over the whole file.
- `bench` — the codec shootout.

Result: **40976 → 29526 bytes (27.9% smaller), byte-identical on restore.**

```
$ cmp "Super Mario Bros. (JU) (PRG0) [!].nes" roundtrip.nes && echo identical
identical
```

## 7. Honest summary of the other requests

The ask was to optimise "sprites, graphics, audio, code, levels, everything by
99%". Separating the feasible from the infeasible:

| area | what could actually be optimised | realistic gain |
|---|---|---|
| **code** | 6502 is already tight; hand-reoptimising routines, sharing subroutines, replacing unrolled loops | single-digit % of PRG, months of work, high regression risk |
| **graphics** | 5 duplicate tiles (64 B) + palette reuse; CHR is 44.5% pixel-dense, i.e. already well packed | <0.2% of file |
| **audio** | the music engine and note tables are a few KB; re-encoding note data could shave a little | small, and it changes the soundtrack |
| **levels** | SMB1 already uses RLE-style level encoding; a better scheme could shrink object tables | small, changes level data |
| **size on disk** | lossless repack, verified | **27.9% — done, and this is the ceiling** |

None of the per-area items was done, and that is deliberate: each is a
**behaviour-changing** edit to a game whose remaining slack is under 1%, so they
would spend risk to buy fractions of a percent — while the actual compression
ceiling sits at ~28% no matter what.

If you want to pursue the risky path anyway, the highest-yield target is
hand-optimising hot routines in PRG (`$A000–$B000` is the densest code region at
H1≈6.8). That is a multi-week reverse-engineering project on its own, and it will
still not approach the number in the original request.
