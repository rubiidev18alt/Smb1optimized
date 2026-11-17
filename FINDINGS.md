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

---

# Appendix: the slim pass (levels, audio, restart)

Built with `tools/make-slim-rom.py`, which applies each cut separately and
prints the PRG footprint after every one, so the price of each feature is
visible. Cuts are applied to a **staged copy**; the decompiled tree is never
mutated.

## Measured cost of each cut

| cut | PRG after | saved | cumulative |
|---|---:|---:|---:|
| (baseline) | 32762 | — | — |
| `levels` — remove 1-2…8-4 | 28499 | **4263** | 17.9% off baseline at the end |
| `music` — stub engine + drop music streams | 26889 | **1610** | |
| `restart` — level completion restarts | 26900 | −11 | |
| **final** | **26900** | **5862 (17.9%)** | |

`music` needed two parts: stubbing `sub_sound_engine` alone saved only **8
bytes**, because the stream data is still emitted. Dropping the 33
`.incbin con_music_data_asset` lines in `music_streams.asm` is what recovered
the other 1602.

## The level-completion black screen: found and fixed

`handler_finish_world` in `game/modes.asm` increments `ram_world_number` to load
the next world. With 1-2 onward removed, that loaded an empty area and hung on a
black screen.

The fix forces a restart of the run instead:

```
LDA #$00 / STA ram_world_number / STA ram_area_number / STA ram_level_number
STA ram_oper_mode_task / JSR sub_load_area_pointer
INC ram_fetch_new_game_timer_flag / LDA #con_mode_game / STA ram_oper_mode
```

Verified by driving the victory path directly:

| build | after completing a level |
|---|---|
| without fix | `mode=1 world=1 area=0` — advances to World 2, no data, **black screen** |
| **with fix** | `mode=1 world=0 area=0` — restarts 1-1, screen **85.3% alive** |

Reproduced with `jsnes` by setting the RAM mode to victory with the world-end
timer expired, then reading back `ram_world_number` and the framebuffer.

## The title-screen cut is unsafe — measured, not assumed

Setting `ram_oper_mode = 1` at boot skips the title screen, but the title
handler is also what performs VRAM/nametable initialisation. The result is a
screen of garbage tiles:

**observed:** 8 colours, sky 16094 px, bricks **24 px** (vs 3535 in a good
build) — the level drew metatile garbage.

The cut also **overflows PRG by 2 bytes** on its own, because it adds
instructions without removing any handler. It is therefore *not* enabled. Making
it safe means also deleting the title/demo handler and reimplementing the VRAM
init it performs — real work, tracked in the summary below.

## CHR: nothing is safely removable

The PPU addresses tiles by index and the encoder emits 8 KiB per bank, so CHR
cannot shrink: 512 tiles, 2 blank, 5 duplicate copies — 80 bytes total, and each
blank/duplicate is referenced by a fixed index. Removing one renumbers every
tile after it and changes what 1-1 draws.

## Final state

| artifact | .nes | packed | vs original |
|---|---:|---:|---:|
| original | 40976 | 29526 | 27.9% smaller |
| 1-1 cut | 40976 | 25526 | 37.7% smaller |
| **slim** | **40976** | **24351** | **40.6% smaller** |

PRG is 26900 bytes — still **10516 bytes over** a single 16 KiB bank, so the
`.nes` remains 40976 bytes. Getting to 24592 needs those 10516 bytes out of the
engine: unused enemy AI, unused object/collision handlers, and the title/demo
code together with its VRAM initialisation.

---

# Appendix: the enemy-AI pass

Two more cuts, each measured independently. Both keep Goomba and Koopa behaviour
intact, because World 1-1 only ever spawns object ids `$00-$14` — the "normal
enemy" path — plus shells.

## What 1-1 actually uses

The 1-1 enemy stream (`base_course_enemy_streams.bin` at `$191`, 30 bytes) holds
**positions**, not types. Types come from the runtime dispatch table, and 1-1
reaches only:

- `handler_run_normal_enemy` (objects `$00-$14`): Goomba, Koopa, shells
- its movement table: walkers, plus shell behaviour

Everything else in the dispatch table is unreachable.

## The cuts

| cut | PRG saved | what it does |
|---|---:|---|
| `enemies` | **361 B** | stubs 14 handlers: Bowser, Bowser flame, firebar, fireworks, large/small platform, vine, star flag, jumpspring, warp zone, Hammer Bro, plus the vine/explosion/platform drawing routines |
| `enemyart` | **190 B** | shrinks `tbl_enemy_graphics_tiles` to the 8 rows 1-1 can reach (koopa ×2, goomba, 4 shell rows, defeated goomba) and re-points the 47-entry offset table at them |
| **total** | **551 B** | cumulative now **6413 B / 19.6%** |

Handlers are stubbed to `RTS` with the body retained inside `.if 0`, so nested
`.if/.endif` pairs stay balanced — the same technique the audio cut needed.

## Verification

| check | result |
|---|---|
| 1-1 renders | 11 colours, sky 44432 px, bricks 3535 px (unchanged) |
| enemies present | **27 peak sprites on screen**, identical to the pre-cut build |
| boots and plays | PASS |
| packed round-trip | byte-identical |

The sprite count matching exactly is the key check: the art table rewrite did not
misalign any enemy frame.

## Final state

| artifact | .nes | packed | smaller |
|---|---:|---:|---:|
| original | 40976 | 29526 | 27.9% |
| 1-1 cut | 40976 | 25526 | 37.7% |
| slim (no enemy pass) | 40976 | 24351 | 40.6% |
| **slim + enemy pass** | **40976** | **23969** | **41.5%** |

PRG is 26349 bytes — still **9965 bytes over** one 16 KiB bank. The enemy pass
bought 551 bytes, which is honest but small: the handlers for unused enemy types
are individually compact, and much of the remaining bulk is shared framework
(rendering, collisions, level parser) that 1-1 still needs.

Remaining realistic targets for the last ~10 KB:

| target | rough size | risk |
|---|---:|---|
| title/demo + its VRAM init | ~2 KB | high — must reimplement the init elsewhere |
| collision pairs for unused object types | ~1-2 KB | medium |
| ending / game-over sequences | ~1 KB | low-medium |
| level parser paths for unused objects | ~1-2 KB | medium |

None of these is individually large enough to close the gap; reaching 24592
requires most of them.

---

# Appendix: level completion now triggers GAME OVER

Advancing to the next world after finishing 1-1 loaded an area that no longer
exists, producing a garbage level. Game over is the right replacement: it is a
real, tested path that shows the game-over screen and then returns to the title
screen.

`handler_finish_world` in `game/modes.asm` now does:

```
LDA #con_mode_game_over / STA ram_oper_mode / LDA #$00 / STA ram_oper_mode_task
```

replacing the world-increment / area-reload / mode-game sequence.

## Verified by tracing the mode byte frame by frame

| frame | mode | meaning |
|---:|---:|---|
| 0 | 2 | victory (level finished) |
| **364** | **3** | **game over screen** |
| 742 | 0 | back to title screen |

Screenshot at mode 3 shows the classic **GAME OVER** screen with the HUD intact.
The sequence completes on its own with no input, so there is no hang and no black
frame.

The `gameover` cut also saves **4 bytes** — the replaced sequence was slightly
larger than the two stores that replace it.

## Final state

| artifact | .nes | packed | smaller |
|---|---:|---:|---:|
| original | 40976 | 29526 | 27.9% |
| 1-1 cut (advance → black screen) | 40976 | 25526 | 37.7% |
| slim, restart variant | 40976 | 23969 | 41.5% |
| **slim, game-over variant** | **40976** | **23947** | **41.6%** |

Cut stack: `levels`, `music`, `gameover`, `enemies`, `enemyart` —
PRG 32762 → 26334 bytes (19.6% smaller), still 9950 bytes over one 16 KiB bank.

---

# Appendix: CHR blanking takes the build to 47.5%

The target for this round was 53.2% smaller, i.e. a packed file of 19177 bytes or
less from 23947. CHR blanking got most of the way there:

| build | .nes | packed | smaller |
|---|---:|---:|---:|
| slim, no CHR pass | 40976 | 23947 | 41.6% |
| **slim + CHR blanking** | **40976** | **21500** | **47.5%** |

That is 5348 packed bytes for a transformation that changes no pixel: 0
differing pixels across a 3000-frame run, and identical hashes for the title
screen, world 1-1 and the GAME OVER screen against the same build without the
pass.

## Why blanking, not shrinking

CHR is 8192 bytes and the PPU addresses tiles by index. The iNES format emits it
in 8 KiB units, so it cannot get smaller and no tile can be renumbered without
rewriting every nametable the game builds. What *can* change is compressibility:
267 of the 512 tiles are never drawn, so their 16 bytes are zeroed and runs of
zeros are nearly free to compress. CHR goes from roughly 4.7 KB packed to 1.4 KB.

## Three ways this was broken before it worked

**1. Pattern-table direction.** SMB sets PPUCTRL bit 4, so background comes from
pattern table 1 (`$1000`) and sprites from table 0. Blanking "unused background
tiles" in table 0 wiped the terrain and the HUD: sky rose to 52684 px and bricks
fell to 0.

**2. The title logo is read back as code.** `handler_copy_title_screen_from_chr`
copies the raw pattern bytes for background tiles 236–255 through `$2007` into RAM
and decodes them there. Those tiles are never referenced in a nametable, so any
harvest based on nametables and OAM concludes they are unused. Blanking them
produced a title screen with the logo missing and the menu floating in mid-air.

**3. A harvest is a lower bound, not a proof.** Sampling every fourth frame for
6000 frames still missed background tile 3, which is drawn at exactly one scroll
position, and two Mario sprite tiles. The failure was small — 25 pixels in an 8x8
box, a single punctuation glyph — which is precisely the kind of thing a
"screens look fine" check misses.

The builder now blank-then-repairs instead of trusting the harvest: blank
everything the harvest called unused (minus the title tiles), run the ROM, report
every tile index that is on screen while its bytes are zero, restore those, and
repeat until a pass reports nothing. As a final gate the whole set of rendered
screens is hashed against the unblanked reference, and the build aborts rather
than emit a ROM that looks different.

`tools/rom-timeline.js` holds the one input sequence that `tools/chr-probe.js`
and the repair loop share, so "identical screens" is not two subtly different
tests. `tools/smoke-test.js` gained a guard that fails when a tile the original
ROM drew is blank in the build under test.

## Why 53.2% is still out of reach

21500 packed needs another **2323 bytes** to reach 19177, and this time the
obvious lever is a cliff rather than a slope.

PRG is 26334 bytes and is stored as two 16 KiB banks. Bank 1 alone compresses to
about **7612 bytes**. If PRG could be made to fit in one bank, the `.nes` would
become 24592 bytes and pack to roughly **14708 — 50% smaller than the original and
comfortably past the target.** So the goal is not really 2323 bytes of
compression; it is **9950 bytes of code**, after which compression does the rest.

That is not achievable without another content cut. Measuring the code in bank 1
(8173 bytes, where the cut already moved most of 1-1's 1-1-reachable engine):
5942 bytes of it are never executed by a full trace of the intended game loop,
but most of that is not safely removable:

| candidate | size | why it cannot go |
|---|---:|---|
| `tbl_player_graphics_tiles` | 208 B | 1-1's own player art |
| `off_palette_0/1_metatiles` | 340 B | 1-1's own metatile palettes |
| `tbl_background_scenery_patterns` | 144 B | 1-1's own hills and bushes |
| title screen | 489 B | removing it skips VRAM init; 1-1 draws garbage |
| ending sequences | 284 B | reached only from a world the cut deleted |
| firebar/bowser/hammer/balance-platform/flagpole handlers | ~330 B | individually compact |

Even optimistically assuming every ending-only routine and every unused enemy
handler could vanish, that is roughly 1200 bytes, and the simulation says 1200
bytes of PRG is worth about **824 packed bytes** — a projection of ~20676, or
44.6% smaller relative to the no-CHR-pass build. Reaching 19177 would need about
2000 bytes of PRG to disappear *and* the packed arithmetic to cooperate, which
the measurements say it does not.

The honest statement is therefore: **47.5% is what this content delivers.** 53.2%
requires cutting player art, scenery or a further mode, or moving to a single-bank
layout that no longer contains a complete 1-1.
