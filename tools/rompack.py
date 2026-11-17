#!/usr/bin/env python3
"""
rompack.py -- analysis + size-optimisation toolkit for NES ROMs.

Design notes
------------
An NES ROM is 6502 machine code plus lookup tables and CHR tile data. Unlike a
text file it has no "loose" content to trim: the program counter has to land on
every byte it lands on, so any structural edit risks changing behaviour. What we
CAN do losslessly and safely is:

  1. Measure     -- prove where the bytes actually are and how compressible.
  2. Deduplicate -- find byte-identical CHR tiles / PRG blocks that can be
                    aliased away (only safe if nothing loads them separately).
  3. Repack      -- store the ROM as independent entropy-coded streams, each
                    with its own tuned context model, and restore it
                    byte-for-byte on load.

Streams are compressed with raw LZMA2 (FORMAT_RAW) so we pay no container
overhead beyond our own compact 32-ish byte header, and each stream gets its own
lc/lp/pb context parameters, which is measurably better than one global setting.
The output is byte-identical after `unpack` -- verified against the original
CRC32 on every round-trip.

Usage
-----
  ./rompack.py analyze ROM.nes
  ./rompack.py pack    ROM.nes -o out.smbz
  ./rompack.py unpack  out.smbz -o ROM.nes
  ./rompack.py bench   ROM.nes
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import lzma
import math
import os
import struct
import sys
import zlib

MAGIC = b"NSQ1"
# tag, lc, lp, pb, raw crc32, raw length, payload length
ENTRY = "<BBBBI I I"
ENTRY_SIZE = 16
INSNES = b"NES\x1a"
PRG_UNIT = 16384
CHR_UNIT = 8192

# (lc, lp, pb) candidates. lc+lp <= 4 is the liblzma limit. SMB1 code likes a
# small literal context (lc=1); CHR tile data likes lc=1 too; both prefer pb=0
# because byte positions carry no positional meaning in this kind of data.
CTX_CANDIDATES = [(lc, lp, pb)
                  for lc in range(0, 5)
                  for lp in range(0, 3)
                  for pb in range(0, 3)
                  if lc + lp <= 4]

DICT_SIZE = 1 << 24  # 16 MiB: far larger than any NES ROM, so no truncation


# --------------------------------------------------------------------------- #
# ROM parsing
# --------------------------------------------------------------------------- #

class NesRom:
    """An iNES image, split into the pieces that matter for compression."""

    def __init__(self, data: bytes):
        if data[:4] != INSNES:
            raise ValueError("not an iNES ROM (bad magic; FDS/UNIF/nes2.0-in-header?)")
        if len(data) < 16:
            raise ValueError("truncated iNES header")
        self.raw = data
        self.header = data[:16]
        self.prg_banks = data[4]
        self.chr_banks = data[5]
        if self.prg_banks == 0:
            raise ValueError("PRG size 0: not a standard iNES layout")
        prg_len = self.prg_banks * PRG_UNIT
        chr_len = self.chr_banks * CHR_UNIT
        self.prg = data[16:16 + prg_len]
        self.chr = data[16 + prg_len:16 + prg_len + chr_len]
        self.trailer = data[16 + prg_len + chr_len:]
        if len(self.prg) != prg_len or len(self.chr) != chr_len:
            raise ValueError("file shorter than iNES header claims")

    @property
    def mapper(self) -> int:
        f6, f7 = self.header[6], self.header[7]
        return (f7 & 0xF0) | (f6 >> 4)

    @property
    def mirroring(self) -> str:
        return "vertical" if self.header[6] & 1 else "horizontal"

    def describe(self) -> str:
        return (f"mapper {self.mapper}, {self.prg_banks*16} KiB PRG, "
                f"{self.chr_banks*8} KiB CHR, {self.mirroring} mirroring, "
                f"battery={'yes' if self.header[6] & 2 else 'no'}")


def load(path: str) -> NesRom:
    with open(path, "rb") as f:
        return NesRom(f.read())


# --------------------------------------------------------------------------- #
# Entropy / structure measurement
# --------------------------------------------------------------------------- #

def entropy(buf: bytes) -> float:
    """Order-0 Shannon entropy in bits/byte."""
    if not buf:
        return 0.0
    counts = collections.Counter(buf)
    n = len(buf)
    return -sum(v / n * math.log2(v / n) for v in counts.values())


def cond_entropy(buf: bytes, order: int) -> float:
    """Empirical H(x|prev `order` bytes) with add-0.5 smoothing.

    This is the rate a strong context-mixing coder would approach; it gives a
    defensible floor for "how small could this possibly get", independent of the
    compressor we happen to run today.
    """
    if order == 0:
        return entropy(buf)
    ctx: dict[bytes, collections.Counter] = collections.defaultdict(collections.Counter)
    for i in range(order, len(buf)):
        ctx[buf[i - order:i]][buf[i]] += 1
    total_bits = 0.0
    n = 0
    for counter in ctx.values():
        s = sum(counter.values())
        n += s
        for v in counter.values():
            total_bits += -v * math.log2(v / (s + 128.0))
    return total_bits / n if n else 0.0


def longest_run(buf: bytes, value: int, minimum: int = 8):
    """Longest run of a repeated byte -- finds padding that could be dropped."""
    best = 0
    run = 0
    for b in buf:
        if b == value:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best if best >= minimum else 0


def duplicate_report(buf: bytes, chunk: int) -> tuple[int, int, int]:
    """Return (chunks, unique, duplicate_bytes) for fixed-size chunking."""
    if len(buf) < chunk:
        return 0, 0, 0
    chunks = [buf[i:i + chunk] for i in range(0, len(buf) - chunk + 1, chunk)]
    counts = collections.Counter(chunks)
    dup = sum((n - 1) * chunk for n in counts.values() if n > 1)
    return len(chunks), len(counts), dup


# --------------------------------------------------------------------------- #
# Packing: raw LZMA2 with per-stream tuned context
# --------------------------------------------------------------------------- #

def _filters(lc: int, lp: int, pb: int) -> list:
    return [{"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME,
             "dict_size": DICT_SIZE, "lc": lc, "lp": lp, "pb": pb}]


def compress_stream(seg: bytes) -> tuple[bytes, int, int, int]:
    """Pick the smallest (lc,lp,pb) for this stream; return raw bytes + params."""
    best = None
    for lc, lp, pb in CTX_CANDIDATES:
        try:
            blob = lzma.compress(seg, format=lzma.FORMAT_RAW,
                                 filters=_filters(lc, lp, pb))
        except (lzma.LZMAError, ValueError):
            continue
        if best is None or len(blob) < len(best[0]):
            best = (blob, lc, lp, pb)
    if best is None:
        raise RuntimeError("no usable LZMA2 parameter set")
    return best


def decompress_stream(blob: bytes, out_len: int, lc: int, lp: int, pb: int) -> bytes:
    out = lzma.decompress(blob, format=lzma.FORMAT_RAW,
                          filters=_filters(lc, lp, pb))
    if len(out) != out_len:
        raise ValueError(f"stream length mismatch: got {len(out)}, want {out_len}")
    return out


def pack(rom: NesRom) -> bytes:
    """Serialise a ROM to the .smbz container.

    Layout:  magic(4) | version(1) | nstreams(1) | reserved(2)
             header16 bytes + crc32 of original whole file
             per stream: tag(1) lc(1) lp(1) pb(1) crc(4) raw_len(4) zip_len(4)
             concatenated stream payloads
    Everything is little-endian; the container is ~40 bytes plus the payload,
    which is why we use raw LZMA2 streams instead of .xz/.7z framing.
    """
    segments = [("H", rom.header), ("P", rom.prg)]
    if rom.chr:
        segments.append(("C", rom.chr))
    if rom.trailer:
        segments.append(("T", rom.trailer))

    index = bytearray()
    payload = bytearray()
    for tag, seg in segments:
        blob, lc, lp, pb = compress_stream(seg)
        index += struct.pack(ENTRY, ord(tag), lc, lp, pb,
                             zlib.crc32(seg), len(seg), len(blob))
        payload += blob

    out = bytearray()
    out += MAGIC
    out += bytes([1, len(segments), 0, 0])
    out += struct.pack("<I", zlib.crc32(rom.raw))
    out += index
    out += payload
    return bytes(out)


def unpack(blob: bytes) -> bytes:
    """Rebuild the exact original ROM image from a .smbz container."""
    if blob[:4] != MAGIC:
        raise ValueError("not a .smbz file (bad magic)")
    version, nstreams = blob[4], blob[5]
    if version != 1:
        raise ValueError(f"unsupported container version {version}")
    whole_crc = struct.unpack_from("<I", blob, 8)[0]
    entries = []
    off = 12
    for _ in range(nstreams):
        tag, lc, lp, pb, crc, raw_len, zip_len = struct.unpack_from(ENTRY, blob, off)
        off += ENTRY_SIZE
        entries.append((chr(tag), lc, lp, pb, crc, raw_len, zip_len))
    pos = off
    parts: dict[str, bytes] = {}
    for tag, lc, lp, pb, crc, raw_len, zip_len in entries:
        seg = decompress_stream(blob[pos:pos + zip_len], raw_len, lc, lp, pb)
        pos += zip_len
        if zlib.crc32(seg) != crc:
            raise ValueError(f"stream {tag!r} failed CRC32 check")
        parts[tag] = seg
    out = parts.pop("H", b"")
    out += parts.pop("P", b"")
    out += parts.pop("C", b"")
    out += parts.pop("T", b"")
    if zlib.crc32(out) != whole_crc:
        raise ValueError("reconstructed ROM failed whole-file CRC32 check")
    return out


# --------------------------------------------------------------------------- #
# Baselines for comparison
# --------------------------------------------------------------------------- #

def codec_table(data: bytes) -> list[tuple[str, int]]:
    rows = [("uncompressed (this is the ROM as shipped)", len(data))]
    for name, fn in (
        ("zlib -9        (deflate)", lambda b: zlib.compress(b, 9)),
        ("bzip2 -9       (bzip2)", lambda b: __import__("bz2").compress(b, 9)),
        ("lzma -9e  (xz, one stream)", lambda b: lzma.compress(b, preset=9 | lzma.PRESET_EXTREME)),
    ):
        rows.append((name, len(fn(data))))
    rom = NesRom(data)
    rows.append(("rompack (per-stream tuned LZMA2)", len(pack(rom))))
    return rows


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def cmd_analyze(args) -> int:
    rom = load(args.rom)
    raw = rom.raw
    n = len(raw)
    print(f"file        : {args.rom}")
    print(f"size        : {n} bytes")
    print(f"layout      : {rom.describe()}")
    print(f"crc32       : {zlib.crc32(raw):08X}")
    print(f"sha256      : {hashlib.sha256(raw).hexdigest()}")
    print()

    print("--- where the bytes are ---")
    sections = [("iNES header", rom.header), ("PRG (code + data)", rom.prg),
                ("CHR (graphics)", rom.chr)]
    if rom.trailer:
        sections.append(("trailer", rom.trailer))
    for name, seg in sections:
        if not seg:
            continue
        print(f"  {name:<20} {len(seg):>6} bytes  {100*len(seg)/n:>5.1f}%  "
              f"H0={entropy(seg):.3f} bits/byte")
    print()

    print("--- graphics (CHR) ---")
    if rom.chr:
        for label, seg in (("background tiles 0-255", rom.chr[:4096]),
                           ("sprite tiles 256-511", rom.chr[4096:8192])):
            if not seg:
                continue
            total, uniq, dup = duplicate_report(seg, 16)
            blank = collections.Counter(
                seg[i:i + 16] for i in range(0, len(seg) - 15, 16)).get(b"\x00" * 16, 0)
            print(f"  {label:<22} {total:>4} tiles, {uniq:>4} unique, "
                  f"{blank} blank, {dup} redundant bytes")
        lo = sum(bin(x).count("1") for x in rom.chr[0::2])
        hi = sum(bin(x).count("1") for x in rom.chr[1::2])
        cells = len(rom.chr) // 2 * 8
        print(f"  pixel density: plane 0 {100*lo/cells:.1f}%  plane 1 {100*hi/cells:.1f}%")
    else:
        print("  CHR-RAM cartridge: no CHR in file")
    print()

    print("--- dead space / duplication ---")
    ff = longest_run(rom.prg, 0xFF)
    zr = longest_run(rom.prg, 0x00)
    print(f"  longest 0xFF run in PRG : {ff} bytes")
    print(f"  longest 0x00 run in PRG : {zr} bytes")
    for chunk in (16, 32, 64):
        total, uniq, dup = duplicate_report(rom.prg, chunk)
        print(f"  PRG {chunk:>2}-byte blocks      : {total:>5} blocks, {uniq:>5} unique, "
              f"{dup:>5} duplicate bytes")
    print()

    print("--- compressibility (this is the whole story) ---")
    print(f"  {'section':<20} {'H0':>6} {'H1':>6} {'H2':>6} {'H3':>6}")
    for name, seg in (("PRG", rom.prg), ("CHR", rom.chr)):
        if not seg:
            continue
        vals = " ".join(f"{cond_entropy(seg, o):>6.3f}" for o in (1, 2, 3))
        print(f"  {name:<20} {entropy(seg):>6.3f} {vals}")

    h1_bound = (len(rom.prg) * cond_entropy(rom.prg, 1)
                + len(rom.chr) * cond_entropy(rom.chr, 1)) / 8 + 16
    print()
    print(f"  order-1 model floor      : {h1_bound:.0f} bytes "
          f"({100*h1_bound/n:.1f}% of original, {100-100*h1_bound/n:.1f}% smaller)")
    print(f"  -> no lossless representation of this ROM can get near a 99% cut.")
    print()

    print("--- codec comparison ---")
    print(f"  {'codec':<38} {'bytes':>7} {'% of orig':>10} {'reduction':>10}")
    for name, size in codec_table(raw):
        print(f"  {name:<38} {size:>7} {100*size/n:>9.1f}% {100-100*size/n:>9.1f}%")
    return 0


def cmd_pack(args) -> int:
    rom = load(args.rom)
    blob = pack(rom)
    out = args.output or os.path.splitext(args.rom)[0] + ".smbz"
    with open(out, "wb") as f:
        f.write(blob)
    n = len(rom.raw)
    print(f"packed  {args.rom} -> {out}")
    print(f"  {n} bytes -> {len(blob)} bytes  "
          f"({100*len(blob)/n:.1f}% of original, {100-100*len(blob)/n:.1f}% smaller)")
    if not args.no_verify:
        restored = unpack(blob)
        ok = restored == rom.raw
        print(f"  round-trip byte-identical: {'YES' if ok else 'NO'}")
        if not ok:
            print("  ERROR: reconstruction differs from original", file=sys.stderr)
            return 1
    return 0


def cmd_unpack(args) -> int:
    with open(args.packed, "rb") as f:
        blob = f.read()
    data = unpack(blob)
    out = args.output or os.path.splitext(args.packed)[0] + ".nes"
    with open(out, "wb") as f:
        f.write(data)
    print(f"unpacked {args.packed} -> {out} ({len(data)} bytes)")
    print(f"  crc32 {zlib.crc32(data):08X}  sha256 {hashlib.sha256(data).hexdigest()}")
    return 0


def cmd_bench(args) -> int:
    rom = load(args.rom)
    n = len(rom.raw)
    print(f"{'codec':<40} {'bytes':>7} {'%':>7} {'cut':>7}")
    for name, size in codec_table(rom.raw):
        print(f"{name:<40} {size:>7} {100*size/n:>6.1f}% {100-100*size/n:>6.1f}%")
    # show which context won per stream, for the curious
    print()
    for tag, seg in (("PRG", rom.prg), ("CHR", rom.chr)):
        if not seg:
            continue
        blob, lc, lp, pb = compress_stream(seg)
        print(f"{tag}: best lc={lc} lp={lp} pb={pb} -> {len(blob)} bytes "
              f"({100*len(blob)/len(seg):.1f}% of raw)")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Analyse and losslessly repack NES ROMs.")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="report size breakdown, entropy and codec comparison")
    a.add_argument("rom")
    a.set_defaults(func=cmd_analyze)

    k = sub.add_parser("pack", help="compress to .smbz and verify round-trip")
    k.add_argument("rom")
    k.add_argument("-o", "--output")
    k.add_argument("--no-verify", action="store_true")
    k.set_defaults(func=cmd_pack)

    u = sub.add_parser("unpack", help="restore a .smbz to a byte-identical .nes")
    u.add_argument("packed")
    u.add_argument("-o", "--output")
    u.set_defaults(func=cmd_unpack)

    b = sub.add_parser("bench", help="codec shootout")
    b.add_argument("rom")
    b.set_defaults(func=cmd_bench)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
