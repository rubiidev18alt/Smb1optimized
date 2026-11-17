#!/usr/bin/env python3
"""
Tests for tools/smbz2nes.py -- the .smbz -> .nes converter.

Run: python3 tests/test_smbz2nes.py
"""

import os
import struct
import subprocess
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL = os.path.join(ROOT, "tools", "smbz2nes.py")
PACKED = os.path.join(ROOT, "dist", "super-mario-bros.smbz")
ORIGINAL = os.path.join(ROOT, "Super Mario Bros. (JU) (PRG0) [!].nes")

sys.path.insert(0, os.path.join(ROOT, "tools"))
import smbz2nes  # noqa: E402


def run(*args, **kw):
    """Invoke the CLI; return (exit_code, stdout, stderr)."""
    proc = subprocess.run([sys.executable, TOOL] + list(args),
                          capture_output=True, text=True, **kw)
    return proc.returncode, proc.stdout, proc.stderr


def read(path):
    with open(path, "rb") as f:
        return f.read()


# --------------------------------------------------------------------------- #
# library level
# --------------------------------------------------------------------------- #

def test_unpack_matches_original():
    rom = smbz2nes.unpack(read(PACKED))
    assert rom == read(ORIGINAL), "unpacked ROM differs from the original"


def test_unpack_is_deterministic():
    blob = read(PACKED)
    assert smbz2nes.unpack(blob) == smbz2nes.unpack(blob)


def test_rejects_bad_magic():
    try:
        smbz2nes.unpack(b"NOPE" + b"\x00" * 40)
    except smbz2nes.SmbzError as exc:
        assert "magic" in str(exc).lower()
        return
    raise AssertionError("bad magic was accepted")


def test_rejects_short_file():
    try:
        smbz2nes.unpack(b"NSQ1")
    except smbz2nes.SmbzError:
        return
    raise AssertionError("a 4-byte file was accepted")


def test_rejects_bad_version():
    blob = bytearray(read(PACKED))
    blob[4] = 99
    try:
        smbz2nes.unpack(bytes(blob))
    except smbz2nes.SmbzError as exc:
        assert "version" in str(exc).lower()
        return
    raise AssertionError("unknown version accepted")


def test_rejects_truncated_payload():
    blob = read(PACKED)[:15000]
    try:
        smbz2nes.unpack(blob)
    except smbz2nes.SmbzError as exc:
        assert "truncat" in str(exc).lower()
        return
    raise AssertionError("truncated container accepted")


def test_detects_corrupt_payload():
    blob = bytearray(read(PACKED))
    blob[1000] ^= 0xFF
    try:
        smbz2nes.unpack(bytes(blob))
    except smbz2nes.SmbzError:
        return
    raise AssertionError("corrupted payload accepted")


def test_detects_corrupt_stream_checksum():
    """Flip a stored CRC32 so decompression succeeds but verification fails."""
    blob = bytearray(read(PACKED))
    # first entry starts at offset 12; crc field is at +4 (after tag,lc,lp,pb)
    crc_off = 12 + 4
    stored = struct.unpack_from("<I", blob, crc_off)[0]
    struct.pack_into("<I", blob, crc_off, stored ^ 0xFFFFFFFF)
    try:
        smbz2nes.unpack(bytes(blob))
    except smbz2nes.SmbzError as exc:
        assert "crc" in str(exc).lower()
        return
    raise AssertionError("corrupt per-stream CRC was accepted")


def test_no_verify_skips_checks():
    """--no-verify may still succeed on a file whose CRCs were tampered with."""
    blob = bytearray(read(PACKED))
    crc_off = 12 + 4
    stored = struct.unpack_from("<I", blob, crc_off)[0]
    struct.pack_into("<I", blob, crc_off, stored ^ 0xFFFFFFFF)
    rom = smbz2nes.unpack(bytes(blob), strict=False)
    assert rom == read(ORIGINAL)


def test_info_does_not_write():
    with tempfile.TemporaryDirectory() as td:
        before = set(os.listdir(td))
        code, out, _ = run("info", PACKED, cwd=td)
        assert code == 0
        assert "NSQ1" in out
        assert set(os.listdir(td)) == before, "info created files"


# --------------------------------------------------------------------------- #
# CLI level
# --------------------------------------------------------------------------- #

def test_cli_convert_default_name():
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "game.smbz")
        with open(src, "wb") as f:
            f.write(read(PACKED))
        code, out, err = run("convert", src)
        assert code == 0, err
        dest = os.path.join(td, "game.nes")
        assert os.path.exists(dest)
        assert read(dest) == read(ORIGINAL)
        assert "CRC32" in out or "crc32" in out


def test_cli_convert_explicit_output():
    with tempfile.TemporaryDirectory() as td:
        dest = os.path.join(td, "sub", "custom.nes")
        code, _, err = run("convert", PACKED, "-o", dest)
        assert code == 0, err
        assert read(dest) == read(ORIGINAL)


def test_cli_refuses_overwrite_without_force():
    with tempfile.TemporaryDirectory() as td:
        dest = os.path.join(td, "existing.nes")
        with open(dest, "wb") as f:
            f.write(b"sentinel")
        code, _, err = run("convert", PACKED, "-o", dest)
        assert code != 0
        assert "already exists" in err
        assert read(dest) == b"sentinel", "existing file was clobbered"

        code, _, err = run("convert", PACKED, "-o", dest, "--force")
        assert code == 0, err
        assert read(dest) == read(ORIGINAL)


def test_cli_stdout_mode():
    proc = subprocess.run([sys.executable, TOOL, "convert", PACKED, "--stdout"],
                          capture_output=True)
    assert proc.returncode == 0
    assert proc.stdout == read(ORIGINAL)


def test_cli_quiet_prints_only_path():
    with tempfile.TemporaryDirectory() as td:
        dest = os.path.join(td, "q.nes")
        code, out, _ = run("convert", PACKED, "-o", dest, "-q")
        assert code == 0
        assert out.strip() == dest


def test_cli_verify_ok_and_fail():
    code, out, _ = run("verify", PACKED)
    assert code == 0
    assert out.startswith("OK")

    with tempfile.TemporaryDirectory() as td:
        bad = os.path.join(td, "bad.smbz")
        with open(bad, "wb") as f:
            f.write(read(PACKED)[:1000])
        code, _, err = run("verify", bad)
        assert code == 1
        assert "FAIL" in err


def test_cli_batch_into_directory():
    with tempfile.TemporaryDirectory() as td:
        srcs = []
        for name in ("a", "b", "c"):
            p = os.path.join(td, name + ".smbz")
            with open(p, "wb") as f:
                f.write(read(PACKED))
            srcs.append(p)
        outdir = os.path.join(td, "roms")     # does not exist yet
        code, _, err = run("convert", *srcs, "-o", outdir, "-q")
        assert code == 0, err
        produced = sorted(os.listdir(outdir))
        assert produced == ["a.nes", "b.nes", "c.nes"]
        for name in produced:
            assert read(os.path.join(outdir, name)) == read(ORIGINAL)


def test_cli_batch_rejects_nes_shaped_output():
    """With several inputs, -o must be a directory; don't mkdir 'x.nes'."""
    with tempfile.TemporaryDirectory() as td:
        srcs = []
        for name in ("a", "b"):
            p = os.path.join(td, name + ".smbz")
            with open(p, "wb") as f:
                f.write(read(PACKED))
            srcs.append(p)
        target = os.path.join(td, "x.nes")
        code, _, err = run("convert", *srcs, "-o", target)
        assert code == 2
        assert "directory" in err
        assert not os.path.exists(target), "created a directory named x.nes"


def test_cli_batch_rejects_existing_file_output():
    with tempfile.TemporaryDirectory() as td:
        srcs = []
        for name in ("a", "b"):
            p = os.path.join(td, name + ".smbz")
            with open(p, "wb") as f:
                f.write(read(PACKED))
            srcs.append(p)
        blocker = os.path.join(td, "blocker")
        with open(blocker, "wb") as f:
            f.write(b"x")
        code, _, err = run("convert", *srcs, "-o", blocker)
        assert code == 2
        assert "existing file" in err


def test_cli_missing_file_reports_cleanly():
    code, _, err = run("convert", "/nonexistent/nope.smbz")
    assert code == 1
    assert "cannot read" in err


def test_cli_no_args_is_usage_error():
    code, _, _ = run()
    assert code == 2


if __name__ == "__main__":
    failures = 0
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print("PASS %s" % name)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print("FAIL %s: %s" % (name, exc))
    print("\n%d/%d passed" % (len(tests) - failures, len(tests)))
    sys.exit(1 if failures else 0)
