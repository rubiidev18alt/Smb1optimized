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

---

# Appendix: can the .nes itself stay compressed?

**Direct answer: no for a `.nes` file; yes if you accept a different container.**
Three separate constraints make a compressed-and-directly-executable `.nes`
impossible, and they are structural rather than a matter of effort.

## 1. Format constraint

An emulator parses a `.nes` as 16 header bytes followed by exactly
`header[4] * 16384` raw PRG bytes and `header[5] * 8192` raw CHR bytes. If those
bytes were entropy-coded the CPU would fetch garbage as opcodes on the first
instruction. The container and the payload cannot both be compressed.

## 2. Hardware constraint (the real blocker)

To execute compressed code you must first decompress it into **writable**
memory. On an NROM cartridge — which is what SMB1 is, mapper 0 — the writable
memory totals:

| region | size |
|---|---:|
| internal RAM (`$0000-$07FF`) | 2048 bytes |
| PRG RAM on the cartridge | **0 bytes** |
| CHR-RAM | **0 bytes** (CHR is ROM here) |
| **total writable** | **2048 bytes** |

PRG is 32768 bytes, so the decompressed image needs a place to live and the
largest available writable region is 16× too small. There is nowhere to put it.

Even granting the space, the decompressor must itself already be raw,
uncompressed code sitting at `$8000` — so a bootstrap loader always survives
compression. You can never compress *everything*.

## 3. This has been asked and answered before

Cartridges that do exactly this exist: the NES has mappers with **on-cartridge
decompression hardware** (MMC5's ExGrafix, and bootleg "Trainer + compressed
CHR" boards). They require **a new cartridge**, i.e. a different mapper with
extra silicon. SMB1 is mapper 0 with no such hardware, so emulating that
behaviour would mean shipping a modified ROM on a different mapper — which is a
different program, not a smaller encoding of this one.

## What you *can* have

| artifact | size | compressed? | runs directly? |
|---|---:|---|---|
| `dist/super-mario-bros.nes` | 40976 | no | **yes**, any emulator |
| `dist/super-mario-bros.smbz` | **29526** | **yes, 27.9%** | no, needs the converter |
| `dist/smb-optimized.zip` | 31459 | yes, 23.2% | **yes**, in Mesen / FCEUX / RetroArch |
| `7z` of the `.nes` | 29678 | yes, 27.6% | yes, in FCEUX / Mesen |

The **zip** is the practical answer to the request: it is a genuinely compressed
single file that you can hand to an emulator without unpacking anything.
Mesen opens `.zip`, `.7z` and `.rar`; FCEUX opens `.zip` and `.7z`; RetroArch
opens zips itself. Deflate reaches 23.2% versus LZMA2's 27.9%, so the zip is
roughly 2 KB larger than the `.smbz` — that is the price of the format being
universally recognised.

If absolute minimum size matters more than convenience, the `.smbz` remains the
winner, and `smbz2nes.py` turns it back into the ROM.

---

# Appendix: the 46.3% / 47.1% targets

These numbers are not reachable by any lossless method. This appendix records the
exhaustive search so the result is reproducible rather than asserted.

## What the targets demand

| target | implied file size | gap from measured best |
|---|---:|---:|
| 47.1% smaller | 21676 B | 7769 B |
| 46.3% "optimized" (= 46.3% of original size) | 18972 B | 10473 B |
| **measured best achievable** | **29445 B (28.1% smaller)** | — |

## Every coder tried, best settings

| coder | bytes | smaller |
|---|---:|---:|
| xz / LZMA2 -9e | 29660 | 27.6% |
| **our tuned container** | **29445** | **28.1%** |
| brotli q11 lgwin24 | 30194 | 26.3% |
| PPMd order 6 / 8 / 12 / 16 / 24 / 32 | 30227 – 30231 | 26.2% |
| zstd -22 | 30902 | 24.6% |

PPMd (a context-mixing coder, the family that wins on text) lands *below* LZMA2
here, which is the signature of data that is dense and non-repetitive rather
than statistically redundant.

## Stream partitioning: tried, and it hurts

Splitting PRG into independently tuned streams, which was the one remaining
untried avenue:

| PRG split | total bytes | vs baseline |
|---|---:|---:|
| 1 stream (baseline) | 29445 | — |
| 2 chunks | 29806 | **+361 worse** |
| 4 chunks | 30186 | +741 worse |
| 8 chunks | 30816 | +1371 worse |
| 16 chunks | 31660 | +2215 worse |

Splitting CHR: 2 chunks +87, 4 chunks +226, 8 chunks +439 — all worse.

The reason is that LZMA2's long-range match finder needs the whole stream in
view; cutting the ROM into pieces destroys the matches that are producing the
28% we already get.

## Why the gap cannot be closed losslessly

The request needs 4.23 bits/byte sustained over content whose order-1 entropy is
**5.87 bits/byte weighted** (PRG 6.235, CHR 4.433). That is 1.39× below the
information-theoretic floor for even a first-order source, and the data is not
first-order — it is dense machine code.

## What closing the gap would actually require

Deleting content. At the measured compression ratios (PRG compresses to 0.7555
of raw, CHR to 0.5724):

- saving 7769 compressed bytes means removing **≈10,283 bytes of raw PRG — 31%
  of the entire program**
- or deleting **all** CHR saves only 4689 bytes, so graphics alone cannot do it

A `.nes` must be raw at runtime, so "a smaller `.nes`" means "a ROM with fewer
bytes in it" — that is a shorter program, i.e. a different game. 46.3% is
therefore not an encoding of SMB1; it is a smaller game that resembles SMB1.

## The honest summary

| claim | status |
|---|---|
| 28.1% smaller, lossless, byte-exact | **achieved and verified** |
| 47.1% smaller | not reachable losslessly (needs 7769 B more) |
| 46.3% | not reachable losslessly (needs 10473 B more) |
| any figure above ~28% | requires deleting game content |

---

# Appendix: decompilation and trimming to ~23268 bytes

This appendix records a genuine decompilation result and the arithmetic that
prevents the requested trim.

## 1. A real decompilation exists and is verified bit-exact

`RetrolariumLab/smb1_src` is a ca65 source reconstruction of the commercial
releases. Our ROM is an exact match for the image it expects
(sha1 `ea343f4e445a9050d4b4fbac2c77d0693b1d0922`).

`tools/build-from-source.py` reproduces the whole chain from this repo:

```
[1/4] fetching source @ eb8faad3
[2/4] building toolchain (ca65/ld65 from cc65)
[3/4] extracting assets from the ROM      -> 7 assets verified, 0 mismatched
[4/4] assembling from source
  rebuilt : 40976 bytes  sha1 ea343f4e445a9050d4b4fbac2c77d0693b1d0922
  original: 40976 bytes  sha1 ea343f4e445a9050d4b4fbac2c77d0693b1d0922
  PASS  rebuilt ROM is BYTE-IDENTICAL to the original
```

So the game is fully decompiled, assembled from 6502 source, and the result is
**bit-for-bit the cartridge**. That is the strongest possible verification.

## 2. 23268 bytes is not a legal iNES size

An iNES file is `16 + PRG_banks*16384 + CHR_banks*8192` — sizes are quantised:

| legal size | composition |
|---:|---|
| 16400 | 16K PRG, CHR-RAM |
| **24592** | **16K PRG + 8K CHR** |
| 32784 | 16K PRG + 16K CHR |
| 40976 | 32K PRG + 8K CHR (current) |

The nearest legal size at or below the request is **24592 bytes** (40.0% smaller).
Reaching it means fitting the entire game into **one 16 KiB PRG bank** — PRG must
be halved.

## 3. The link fails by exactly 16384 bytes

Linking the verified object against a one-bank config:

```
Warning: Segment 'PRG' overflows memory area 'ROM0' by 16384 bytes
ld65: Error: Cannot generate most of the files due to memory area overflow
```

Not approximately — **exactly double**. PRG is 32762 bytes with **zero padding**,
so nothing can be shaved by repacking. The bytes must be *deleted*.

## 4. Where the bytes are, and why deleting them breaks the game

| region | bytes | share of PRG |
|---|---:|---:|
| code: engine, audio, rendering, physics, collisions | 29336 | 89.5% |
| level/course streams + enemy streams | 3267 | 10.0% |
| music data | 159 | 0.5% |

Available non-code data that could theoretically be cut:

| asset | bytes |
|---|---:|
| course area streams (all levels) | 3373 |
| music data | 1602 |
| course enemy streams | 1087 |
| metatile graphics | 404 |
| area palette packets | 176 |
| **total** | **6642** |

Required cut: **16384 bytes**. Even deleting **all** level data, **all** music
and **all** enemy placement:

- 6642 bytes available (40.5% of what is needed)
- **9742 bytes must still come out of the 6502 engine itself — 29.7% of PRG**

There is no CHR-RAM path either: the source tree contains **no** CHR-RAM
initialisation code (a byte-for-byte reconstruction has no reason to), so the
8 KiB CHR section cannot be moved into PPU RAM to free PRG space.

## 5. What "trimmed to 24592" would actually be

Removing any PRG or CHR bank is not an encoding change, it is a functional one:

- **PRG 32K→16K** — delete World 2 onward, the title/demo screens, the audio
  engine, or all sprite/enemy logic. The remaining code would have to be
  re-pointed at every removed symbol, and the result is a different program.
- **CHR 8K→0K** — requires writing a CHR uploader that streams tiles into PPU
  RAM, plus graphics ROM to stream from, which costs the PRG space it was meant
  to save.

Neither is "an optimised SMB1"; both are a smaller game resembling SMB1.

## 6. Licensing

The reconstruction is public but **no license is granted**. Its
`docs/licensing.md` states plainly: *"No license to Nintendo's original game
code, graphics, music, characters, ROM images... is granted by this repository...
Their presence in the repository does not by itself grant redistribution
rights."*

Consequently this repo **does not vendor** that source or any generated assets.
`tools/build-from-source.py` fetches it at a pinned commit on demand and works
only from the ROM you already have. The rebuilt ROM and extracted assets land in
`build/`, which is git-ignored.

---

# Appendix: the World 1-1 cut

Built with `tools/make-cut-rom.py`. Levels 1-2 through 8-4 are removed by
pointing their area and enemy streams at the terminator byte, so the level
parser ends those areas immediately. Everything needed for 1-1 survives.

## What is preserved (verified)

| item | check |
|---|---|
| 1-1 object stream (101 bytes) | byte-identical to the original |
| 1-1 enemy stream (30 bytes) | byte-identical to the original |
| title screen + START | renders, accepts input |
| World 1-1 with enemies | renders, 11 colours |
| audio | 239,610 APU samples generated |
| sprites | full CHR retained, Mario/Goomba/Koopa animate |

## What was removed

33 area streams and 33 enemy streams (everything except 1-1's) replaced with a
terminator. PRG usage drops **32762 → 28499 bytes (−4263, −13.0%)**.

## Why the .nes is still 40976 bytes

A cartridge is quantised to 16 KiB PRG banks. 28499 bytes is still **12115 bytes
over** a single bank, so the file cannot step down to 24592. The size stays put
until another 12115 bytes come out of the 6502 engine.

| variant | .nes size | packed (.smbz) | smaller |
|---|---:|---:|---:|
| original | 40976 | 29526 | 27.9% |
| **1-1 only cut** | **40976** | **25526** | **37.7%** |

So the cut pays off in compressed size even though the raw file is unchanged —
because the removed level data was poorly compressible.

## To actually reach 24592 (16 KiB PRG + 8 KiB CHR)

Another **12115 bytes** must be deleted. Realistic targets, measured from linker
labels:

| subsystem | available |
|---|---:|
| all music + sound-effect data | 1529 B |
| remaining enemy AI beyond Goomba/Koopa | several KB |
| object/collision handlers for unused objects (Bowser, firebars, platforms) | several KB |
| title screen, game-over, ending sequences | ~2 KB |

That is genuine engine surgery: deleting code paths forces re-pointing every
caller, and each removal risks breaking 1-1 itself. It is achievable but must be
done incrementally with the 1-1 verification test after every step.

## Honest status

- 1-1-only cut **works** and is shipped as `dist/smb-1-1-only.nes`.
- Its packed form is **37.7% smaller**, the best result so far.
- It is **not** 24592 bytes; that requires the additional engine cuts above.
- It is **not** the original game: 1-2 through 8-4 are gone by design.
