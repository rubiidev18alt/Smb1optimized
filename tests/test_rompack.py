#!/usr/bin/env python3
"""Tests for tools/rompack.py -- correctness of the container round-trip.

Run: python3 -m pytest tests/ -q     (or)     python3 tests/test_rompack.py
"""

import os
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import rompack  # noqa: E402

ROM = os.path.join(os.path.dirname(__file__), "..",
                   "Super Mario Bros. (JU) (PRG0) [!].nes")


def _rom_bytes():
    with open(ROM, "rb") as f:
        return f.read()


def test_roundtrip_is_byte_identical():
    raw = _rom_bytes()
    rom = rompack.NesRom(raw)
    packed = rompack.pack(rom)
    assert rompack.unpack(packed) == raw


def test_packing_actually_shrinks():
    raw = _rom_bytes()
    packed = rompack.pack(rompack.NesRom(raw))
    assert len(packed) < len(raw)
    # This ROM cannot compress below ~72%; guard against silent regressions.
    assert len(packed) < 30000, f"packed size regressed: {len(packed)}"


def test_rom_identity():
    """Lock the known-good ROM so the tests mean something."""
    raw = _rom_bytes()
    assert len(raw) == 40976
    assert zlib.crc32(raw) == 0x3337EC46


def test_header_and_layout_parsed():
    rom = rompack.NesRom(_rom_bytes())
    assert rom.mapper == 0
    assert rom.prg_banks == 2
    assert rom.chr_banks == 1
    assert len(rom.prg) == 32768
    assert len(rom.chr) == 8192


def test_corruption_is_detected():
    raw = _rom_bytes()
    packed = bytearray(rompack.pack(rompack.NesRom(raw)))
    packed[-20] ^= 0xFF  # flip a byte inside the payload
    try:
        rompack.unpack(bytes(packed))
    except Exception:
        return
    raise AssertionError("corrupted container was accepted silently")


def test_entry_struct_size_matches():
    """The 16-byte entry format is duplicated in pack/unpack; keep it honest."""
    import struct
    assert struct.calcsize(rompack.ENTRY) == rompack.ENTRY_SIZE
    assert rompack.ENTRY_SIZE == 16


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {exc}")
    print("\nall tests passed" if not failures else f"\n{failures} test(s) failed")
    sys.exit(1 if failures else 0)
