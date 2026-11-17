#!/usr/bin/env python3
"""
smbz2nes.py -- convert .smbz files back into .nes ROMs.

Self-contained: Python 3.6+ standard library only. No repo checkout, no pip
install, no third-party LZMA binding required.

The .smbz format stores each ROM section (iNES header, PRG, CHR) as its own raw
LZMA2 stream with tuned context parameters. Reassembly is deterministic, and
every stream plus the final file is checksummed, so a damaged or truncated
download is reported instead of silently producing a bad ROM.

Usage
-----
  smbz2nes.py convert game.smbz                 -> game.nes
  smbz2nes.py convert game.smbz -o out.nes
  smbz2nes.py convert *.smbz -o roms/           # batch into a directory
  smbz2nes.py verify game.smbz                  # integrity check, writes nothing
  smbz2nes.py info game.smbz                    # what is inside the container

Exit codes: 0 success, 1 conversion/integrity failure, 2 usage error.
"""

from __future__ import annotations

import argparse
import glob
import lzma
import os
import struct
import sys
import zlib

MAGIC = b"NSQ1"
VERSION = 1
# tag, lc, lp, pb, crc32 of raw, raw length, payload length
ENTRY = "<BBBBI I I"
ENTRY_SIZE = struct.calcsize(ENTRY)
DICT_SIZE = 1 << 24
SECTION_NAMES = {"H": "iNES header", "P": "PRG (code + data)",
                 "C": "CHR (graphics)", "T": "trailer"}


class SmbzError(Exception):
    """Raised for any malformed or corrupt container."""


def _filters(lc: int, lp: int, pb: int) -> list:
    return [{
        "id": lzma.FILTER_LZMA2,
        "preset": 9 | lzma.PRESET_EXTREME,
        "dict_size": DICT_SIZE,
        "lc": lc, "lp": lp, "pb": pb,
    }]


def read_header(blob: bytes) -> tuple[int, int, int]:
    """Return (version, stream_count, whole_file_crc32)."""
    if len(blob) < 12:
        raise SmbzError("file is too short to be a .smbz (needs at least 12 bytes)")
    if blob[:4] != MAGIC:
        raise SmbzError("not a .smbz file (bad magic %r, expected %r)"
                        % (blob[:4], MAGIC))
    version, count = blob[4], blob[5]
    if version != VERSION:
        raise SmbzError("unsupported container version %d (this tool reads %d)"
                        % (version, VERSION))
    if count == 0:
        raise SmbzError("container declares zero streams")
    whole_crc = struct.unpack_from("<I", blob, 8)[0]
    return version, count, whole_crc


def read_index(blob: bytes, count: int):
    """Parse the per-stream index. Returns (entries, payload_offset)."""
    entries = []
    off = 12
    for i in range(count):
        if off + ENTRY_SIZE > len(blob):
            raise SmbzError("container index is truncated at stream %d" % i)
        tag, lc, lp, pb, crc, raw_len, zip_len = struct.unpack_from(ENTRY, blob, off)
        off += ENTRY_SIZE
        try:
            name = chr(tag)
        except ValueError:
            raise SmbzError("stream %d has an invalid tag %d" % (i, tag))
        if lc + lp > 4:
            raise SmbzError("stream %r has impossible LZMA parameters lc=%d lp=%d"
                            % (name, lc, lp))
        entries.append({"tag": name, "lc": lc, "lp": lp, "pb": pb,
                        "crc": crc, "raw_len": raw_len, "zip_len": zip_len})
    return entries, off


def unpack(blob: bytes, strict: bool = True) -> bytes:
    """Reassemble the ROM. Verifies every stream and the whole file when strict."""
    _version, count, whole_crc = read_header(blob)
    entries, pos = read_index(blob, count)

    declared = sum(e["zip_len"] for e in entries)
    if pos + declared > len(blob):
        raise SmbzError("file is truncated: header declares %d payload bytes, "
                        "only %d present" % (declared, len(blob) - pos))

    parts = {}
    for e in entries:
        name, zip_len, raw_len = e["tag"], e["zip_len"], e["raw_len"]
        chunk = blob[pos:pos + zip_len]
        pos += zip_len
        try:
            seg = lzma.decompress(chunk, format=lzma.FORMAT_RAW,
                                  filters=_filters(e["lc"], e["lp"], e["pb"]))
        except lzma.LZMAError as exc:
            raise SmbzError("stream %r failed to decompress: %s" % (name, exc))
        if len(seg) != raw_len:
            raise SmbzError("stream %r decompressed to %d bytes, expected %d"
                            % (name, len(seg), raw_len))
        if strict and zlib.crc32(seg) != e["crc"]:
            raise SmbzError("stream %r failed its CRC32 check (corrupt data)"
                            % name)
        parts[name] = seg

    out = (parts.pop("H", b"") + parts.pop("P", b"")
           + parts.pop("C", b"") + parts.pop("T", b""))
    if not out:
        raise SmbzError("container held no data")
    if strict and zlib.crc32(out) != whole_crc:
        raise SmbzError("reassembled ROM failed the whole-file CRC32 check")
    return out


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def _target_for(src: str, out_arg: str | None, multiple: bool) -> str:
    stem = os.path.splitext(os.path.basename(src))[0] + ".nes"
    if out_arg is None:
        return os.path.join(os.path.dirname(os.path.abspath(src)), stem)
    # Batch mode, or the caller passed a directory: put the .nes inside it.
    if multiple or out_arg.endswith(os.sep) or os.path.isdir(out_arg):
        return os.path.join(out_arg, stem)
    return out_arg


def cmd_convert(args) -> int:
    sources = []
    for pattern in args.inputs:
        matched = glob.glob(pattern)
        sources.extend(matched if matched else [pattern])
    if not sources:
        print("error: no input files", file=sys.stderr)
        return 2

    multiple = len(sources) > 1
    # With several inputs, -o is unambiguously a directory. Refuse an obviously
    # file-shaped name (e.g. "-o game.nes") rather than silently mkdir-ing it.
    if multiple and args.output:
        if args.output.lower().endswith(".nes"):
            print("error: %d inputs given, so -o must be a directory, not a "
                  ".nes file path (%s)" % (len(sources), args.output),
                  file=sys.stderr)
            return 2
        if os.path.exists(args.output) and not os.path.isdir(args.output):
            print("error: %d inputs given, so -o must be a directory, but %s "
                  "is an existing file" % (len(sources), args.output),
                  file=sys.stderr)
            return 2
        try:
            os.makedirs(args.output, exist_ok=True)
        except OSError as exc:
            print("error: cannot create output directory %s: %s"
                  % (args.output, exc), file=sys.stderr)
            return 2

    failures = 0
    for src in sources:
        try:
            with open(src, "rb") as f:
                blob = f.read()
        except OSError as exc:
            print("error: cannot read %s: %s" % (src, exc), file=sys.stderr)
            failures += 1
            continue

        try:
            rom = unpack(blob, strict=not args.no_verify)
        except SmbzError as exc:
            print("FAIL %s: %s" % (src, exc), file=sys.stderr)
            failures += 1
            continue

        if args.stdout:
            sys.stdout.buffer.write(rom)
            sys.stdout.buffer.flush()
            continue

        dest = _target_for(src, args.output, multiple)
        if os.path.exists(dest) and not args.force:
            print("FAIL %s: %s already exists (use --force to overwrite)"
                  % (src, dest), file=sys.stderr)
            failures += 1
            continue

        parent = os.path.dirname(os.path.abspath(dest))
        os.makedirs(parent, exist_ok=True)
        try:
            with open(dest, "wb") as f:
                f.write(rom)
        except OSError as exc:
            print("FAIL %s: cannot write %s: %s" % (src, dest, exc), file=sys.stderr)
            failures += 1
            continue

        if args.quiet:
            print(dest)
        else:
            saved = len(rom) - len(blob)   # positive = the .smbz was smaller
            print("%s -> %s" % (src, dest))
            print("  %d bytes  crc32 %08X  sha256 %s"
                  % (len(rom), zlib.crc32(rom), _sha256(rom)))
            print("  verified: %d streams + whole file matched their CRC32"
                  % len(read_index(blob, read_header(blob)[1])[0]))
            if saved > 0:
                print("  unpacked from %d bytes (saved %d, %.1f%%)"
                      % (len(blob), saved, 100.0 * saved / len(rom)))
            elif saved < 0:
                print("  note: this .smbz is larger than the ROM (+%d bytes)"
                      % -saved)

    return 0 if failures == 0 else 1


def cmd_verify(args) -> int:
    failures = 0
    for src in args.inputs:
        try:
            with open(src, "rb") as f:
                blob = f.read()
            rom = unpack(blob, strict=True)
        except (OSError, SmbzError) as exc:
            print("FAIL %s: %s" % (src, exc), file=sys.stderr)
            failures += 1
            continue
        print("OK   %s  (%d bytes, crc32 %08X)" % (src, len(rom), zlib.crc32(rom)))
    return 0 if failures == 0 else 1


def cmd_info(args) -> int:
    for src in args.inputs:
        try:
            with open(src, "rb") as f:
                blob = f.read()
            version, count, whole_crc = read_header(blob)
            entries, payload_off = read_index(blob, count)
        except (OSError, SmbzError) as exc:
            print("error: %s: %s" % (src, exc), file=sys.stderr)
            return 1

        total_raw = sum(e["raw_len"] for e in entries)
        total_zip = sum(e["zip_len"] for e in entries)
        print("%s" % src)
        print("  container   : NSQ1 v%d, %d streams, %d bytes on disk"
              % (version, count, len(blob)))
        print("  header cx   : %d bytes" % payload_off)
        print("  payload     : %d bytes in, %d bytes raw out (%.1f%%)"
              % (total_zip, total_raw, 100.0 * total_zip / max(total_raw, 1)))
        print("  whole crc32 : %08X" % whole_crc)
        print("  %-18s %9s %9s %9s  %s" % ("section", "raw", "stored", "ratio", "lzma ctx"))
        for e in entries:
            ratio = 100.0 * e["zip_len"] / max(e["raw_len"], 1)
            note = ""
            if e["zip_len"] >= e["raw_len"]:
                note = "  (incompressible; LZMA2 framing overhead)"
            print("  %-18s %9d %9d %8.1f%%  lc=%d lp=%d pb=%d%s"
                  % (SECTION_NAMES.get(e["tag"], "stream " + e["tag"]),
                     e["raw_len"], e["zip_len"], ratio,
                     e["lc"], e["lp"], e["pb"], note))
        print()
    return 0


def _sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="smbz2nes.py",
        description="Convert .smbz containers back into .nes ROMs.",
        epilog="The .nes is a byte-exact restoration; every stream and the whole "
               "file are CRC-checked.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("convert", help="write .nes file(s) from .smbz")
    c.add_argument("inputs", nargs="+", metavar="FILE.smbz",
                   help="input file(s); globs are expanded")
    c.add_argument("-o", "--output", help="output .nes path, or a directory")
    c.add_argument("-f", "--force", action="store_true", help="overwrite existing files")
    c.add_argument("-q", "--quiet", action="store_true", help="print only output paths")
    c.add_argument("--stdout", action="store_true",
                   help="write the ROM to stdout instead of a file")
    c.add_argument("--no-verify", action="store_true",
                   help="skip CRC checks (faster, unsafe)")
    c.set_defaults(func=cmd_convert)

    v = sub.add_parser("verify", help="check integrity without writing anything")
    v.add_argument("inputs", nargs="+", metavar="FILE.smbz")
    v.set_defaults(func=cmd_verify)

    i = sub.add_parser("info", help="show what a container holds")
    i.add_argument("inputs", nargs="+", metavar="FILE.smbz")
    i.set_defaults(func=cmd_info)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:      # e.g. `... --stdout | head`
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
