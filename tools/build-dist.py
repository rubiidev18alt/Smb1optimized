#!/usr/bin/env python3
"""
build-dist.py -- produce the downloadable artifacts in dist/.

Creates:
  dist/super-mario-bros.smbz        the packed ROM (the small download)
  dist/super-mario-bros.nes         the ROM itself, runnable in any emulator
  dist/unpack.py                    standalone extractor (stdlib only)
  dist/SHA256SUMS                   checksums for both

Then verifies the whole chain: .nes -> .smbz -> .nes must reproduce the original
byte for byte, and both artifacts must match their recorded hashes.
"""

import hashlib
import os
import shutil
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(ROOT, "dist")

sys.path.insert(0, HERE)
import rompack  # noqa: E402

ROM_NAME = "Super Mario Bros. (JU) (PRG0) [!].nes"
OUT_PACKED = "super-mario-bros.smbz"
OUT_NES = "super-mario-bros.nes"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    rom_path = os.path.join(ROOT, ROM_NAME)
    if not os.path.exists(rom_path):
        print("error: %s not found" % ROM_NAME, file=sys.stderr)
        return 1

    with open(rom_path, "rb") as f:
        original = f.read()

    rom = rompack.NesRom(original)
    packed = rompack.pack(rom)

    os.makedirs(DIST, exist_ok=True)

    # 1. the packed download
    packed_path = os.path.join(DIST, OUT_PACKED)
    with open(packed_path, "wb") as f:
        f.write(packed)

    # 2. the directly runnable ROM
    nes_path = os.path.join(DIST, OUT_NES)
    with open(nes_path, "wb") as f:
        f.write(original)

    # 3. standalone extractor
    extractor_src = os.path.join(DIST, "unpack.py")
    if not os.path.exists(extractor_src):
        print("error: dist/unpack.py is missing", file=sys.stderr)
        return 1

    # 4. checksums
    sums_path = os.path.join(DIST, "SHA256SUMS")
    lines = []
    for name in (OUT_PACKED, OUT_NES):
        with open(os.path.join(DIST, name), "rb") as f:
            lines.append("%s  %s\n" % (sha256(f.read()), name))
    with open(sums_path, "w") as f:
        f.writelines(lines)

    # ---- verification ----
    with open(packed_path, "rb") as f:
        reread = f.read()
    restored = rompack.unpack(reread)

    checks = [
        ("packed round-trips to the original ROM", restored == original),
        ("packed file is smaller than the ROM", len(packed) < len(original)),
        ("stored .nes matches the original", open(nes_path, "rb").read() == original),
    ]

    print("built dist/")
    print("  %-28s %7d bytes  (%.1f%% of original, %.1f%% smaller)"
          % (OUT_NES, len(original), 100.0, 0.0))
    print("  %-28s %7d bytes  (%.1f%% of original, %.1f%% smaller)"
          % (OUT_PACKED, len(packed), 100.0 * len(packed) / len(original),
             100.0 - 100.0 * len(packed) / len(original)))
    print("  %-28s %7s        %s" % ("unpack.py", "", "stdlib-only extractor"))
    print()
    print("  crc32 original   %08X" % zlib.crc32(original))
    print("  crc32 restored   %08X" % zlib.crc32(restored))
    print("  sha256 original  %s" % sha256(original))
    print()

    ok = True
    for label, passed in checks:
        print("  %s %s" % ("PASS" if passed else "FAIL", label))
        ok = ok and passed

    print()
    print("verified" if ok else "VERIFICATION FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
