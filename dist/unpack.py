#!/usr/bin/env python3
"""
unpack.py -- standalone extractor for .smbz files. Python 3 stdlib only.

    python3 unpack.py super-mario-bros.smbz -o super-mario-bros.nes

No third-party libraries, no repo checkout required. Verifies a CRC32 per stream
and a CRC32 over the whole reconstructed ROM, so a corrupt download is caught
instead of producing a broken .nes.
"""

import argparse
import lzma
import os
import struct
import sys
import zlib

MAGIC = b"NSQ1"
ENTRY = "<BBBBI I I"   # tag, lc, lp, pb, crc32, raw_len, zip_len
ENTRY_SIZE = struct.calcsize(ENTRY)
DICT_SIZE = 1 << 24


def _filters(lc, lp, pb):
    return [{
        "id": lzma.FILTER_LZMA2,
        "preset": 9 | lzma.PRESET_EXTREME,
        "dict_size": DICT_SIZE,
        "lc": lc, "lp": lp, "pb": pb,
    }]


def unpack(blob):
    if blob[:4] != MAGIC:
        raise ValueError("not a .smbz file (bad magic)")
    version, nstreams = blob[4], blob[5]
    if version != 1:
        raise ValueError("unsupported container version %d" % version)

    whole_crc = struct.unpack_from("<I", blob, 8)[0]
    entries = []
    off = 12
    for _ in range(nstreams):
        entries.append(struct.unpack_from(ENTRY, blob, off))
        off += ENTRY_SIZE

    pos = off
    parts = {}
    for tag, lc, lp, pb, crc, raw_len, zip_len in entries:
        chunk = blob[pos:pos + zip_len]
        pos += zip_len
        if len(chunk) != zip_len:
            raise ValueError("truncated container: stream %r is short" % chr(tag))
        seg = lzma.decompress(chunk, format=lzma.FORMAT_RAW,
                              filters=_filters(lc, lp, pb))
        if len(seg) != raw_len:
            raise ValueError("stream %r length %d, expected %d"
                             % (chr(tag), len(seg), raw_len))
        if zlib.crc32(seg) != crc:
            raise ValueError("stream %r failed CRC32 verification" % chr(tag))
        parts[chr(tag)] = seg

    out = (parts.pop("H", b"") + parts.pop("P", b"")
           + parts.pop("C", b"") + parts.pop("T", b""))
    if zlib.crc32(out) != whole_crc:
        raise ValueError("reconstructed ROM failed whole-file CRC32 verification")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Extract a .smbz back to a .nes file.")
    ap.add_argument("smbz")
    ap.add_argument("-o", "--output")
    args = ap.parse_args(argv)

    with open(args.smbz, "rb") as f:
        blob = f.read()

    data = unpack(blob)
    out = args.output or os.path.splitext(args.smbz)[0] + ".nes"
    parent = os.path.dirname(os.path.abspath(out))
    os.makedirs(parent, exist_ok=True)
    with open(out, "wb") as f:
        f.write(data)

    print("extracted %s -> %s" % (args.smbz, out))
    print("  size  %d bytes" % len(data))
    print("  crc32 %08X" % zlib.crc32(data))
    print("  verified: every stream and the whole file matched its CRC32")
    return 0


if __name__ == "__main__":
    sys.exit(main())
