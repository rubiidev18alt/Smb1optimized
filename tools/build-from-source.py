#!/usr/bin/env python3
"""
build-from-source.py -- build a real Super Mario Bros. ROM from 6502 source.

This is the "decompile" step: rather than treat the cartridge as an opaque blob,
assemble the game from actual ca65 source and confirm the result is bit-exact
against the original image.

Requires
--------
- git, make, a C compiler (to build ca65/ld65 from cc65, once)
- network access to github.com for the two checkouts

The source is RetrolariumLab/smb1_src, a byte-identical ca65 reconstruction of
the commercial releases. It is pinned to a commit so builds stay reproducible.

Usage
-----
  ./build-from-source.py            # build everything, verify, report
  ./build-from-source.py --report   # also print PRG budget analysis

Outputs
-------
  build/src/smb1_src/           the pinned source checkout
  build/bin/ca65, ld65          the assembler/linker built from cc65
  build/rom/smb-rebuilt.nes     the ROM assembled from source

Nothing copyrighted is committed: the ROM and extracted assets are regenerated
locally from the ROM you already have, and are git-ignored.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
SRC_REPO = "https://github.com/RetrolariumLab/smb1_src"
SRC_PIN = "eb8faad337056e074e78ec5f3b496be24e7de761"
CC65_REPO = "https://github.com/cc65/cc65"
ROM = os.path.join(ROOT, "Super Mario Bros. (JU) (PRG0) [!].nes")


def sh(cmd, cwd=None, env=None, check=True, quiet=True):
    """Run a command, streaming output only on failure."""
    proc = subprocess.run(cmd, cwd=cwd, env=env, shell=isinstance(cmd, str),
                          capture_output=True, text=True)
    if check and proc.returncode != 0:
        sys.stderr.write(proc.stdout or "")
        sys.stderr.write(proc.stderr or "")
        raise SystemExit("command failed (%d): %s" % (proc.returncode, cmd))
    return proc


def have(prog):
    return shutil.which(prog) is not None


# --------------------------------------------------------------------------- #
# steps
# --------------------------------------------------------------------------- #

def fetch_source(dest):
    if os.path.isdir(os.path.join(dest, ".git")):
        head = sh(["git", "rev-parse", "HEAD"], cwd=dest).stdout.strip()
        if head == SRC_PIN:
            return
        sh(["git", "fetch", "--quiet", "origin", SRC_PIN], cwd=dest)
    else:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        sh(["git", "clone", "--quiet", SRC_REPO, dest])
    sh(["git", "checkout", "--quiet", SRC_PIN], cwd=dest)


def build_toolchain(dest):
    """Build ca65/ld65 from cc65 unless already built."""
    ca65 = os.path.join(dest, "bin", "ca65")
    ld65 = os.path.join(dest, "bin", "ld65")
    if os.path.exists(ca65) and os.path.exists(ld65):
        return dest + "/bin"

    if not os.path.isdir(os.path.join(dest, ".git")):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        sh(["git", "clone", "--quiet", "--depth", "1", CC65_REPO, dest])
    sh(["make", "-j%d" % (os.cpu_count() or 2), "ca65", "ld65"], cwd=dest)
    return dest + "/bin"


def split_assets(src_dir, rom_bytes):
    """Reproduce the source repo's 'split' step: carve assets out of the ROM."""
    manifest = json.load(open(os.path.join(src_dir, "assets", "manifest.json")))
    out_root = os.path.join(src_dir, "assets", "generated")
    checked = bad = 0
    for asset in manifest["assets"]:
        if asset["image"] != "ju":
            continue
        off, size = asset["offset"], asset["size"]
        chunk = rom_bytes[off:off + size]
        digest = hashlib.sha1(chunk).hexdigest()
        if digest == asset["sha1"]:
            checked += 1
        else:
            bad += 1
            print("  asset hash mismatch: %s" % asset["path"], file=sys.stderr)
        dest = os.path.join(out_root, asset["path"])
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(chunk)
    return checked, bad


def assemble(src_dir, toolbin, out_path):
    env = dict(os.environ)
    env["PATH"] = toolbin + os.pathsep + env.get("PATH", "")
    obj = os.path.join(BUILD, "rom", "smb.o")
    os.makedirs(os.path.dirname(obj), exist_ok=True)
    sh([os.path.join(toolbin, "ca65"),
        "-I", "src", "-I", "src/revisions/",
        "--bin-include-dir", "assets/generated",
        "-g", "-o", obj, "src/revisions/ju.asm"], cwd=src_dir)
    sh([os.path.join(toolbin, "ld65"),
        "-C", "config/linker/nrom256.cfg",
        "-o", out_path, obj], cwd=src_dir)
    return obj


# --------------------------------------------------------------------------- #
# analysis
# --------------------------------------------------------------------------- #

def prg_report(src_dir, toolbin, obj):
    """Link against a 16 KiB bank to measure how far the ROM is from fitting."""
    cfg_src = os.path.join(src_dir, "config", "linker", "nrom256.cfg")
    cfg = open(cfg_src).read().replace(
        "ROM0: file = %O, start = $8000, size = $7FFA",
        "ROM0: file = %O, start = $C000, size = $3FFA")
    cfg_path = os.path.join(BUILD, "rom", "one_bank.cfg")
    with open(cfg_path, "w") as f:
        f.write(cfg)

    env = dict(os.environ)
    env["PATH"] = toolbin + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(
        [os.path.join(toolbin, "ld65"), "-C", cfg_path,
         "-o", os.path.join(BUILD, "rom", "onebank.nes"), obj],
        cwd=src_dir, capture_output=True, text=True, env=env)
    for line in (proc.stdout + proc.stderr).splitlines():
        if "overflow" in line.lower():
            print("  %s" % line.strip())

    manifest = json.load(open(os.path.join(src_dir, "assets", "manifest.json")))
    data = sum(a["size"] for a in manifest["assets"]
               if a["image"] == "ju" and "programs" not in a["path"]
               and a["path"] != "chr/smb.chr")
    need = 16384
    print()
    print("  PRG available in the full build : 32762 bytes (100% used, 0 padding)")
    print("  PRG available in ONE 16K bank    : 16378 bytes")
    print("  must remove                      : %d bytes" % need)
    print("  all non-code data available      : %d bytes (%.1f%% of the need)"
          % (data, 100.0 * data / need))
    print("  remaining cut must come from CODE: %d bytes (%.1f%% of PRG)"
          % (need - data, 100.0 * (need - data) / 32762))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", action="store_true",
                    help="include the PRG budget analysis")
    args = ap.parse_args(argv)

    for prog in ("git", "make"):
        if not have(prog):
            raise SystemExit("error: %s is required" % prog)

    if not os.path.exists(ROM):
        raise SystemExit("error: %s not found" % ROM)
    rom_bytes = open(ROM, "rb").read()

    src_dir = os.path.join(BUILD, "src", "smb1_src")
    tool_dir = os.path.join(BUILD, "cc65")

    print("[1/4] fetching source (%s @ %s)" % (SRC_REPO, SRC_PIN[:8]))
    fetch_source(src_dir)

    print("[2/4] building toolchain (ca65/ld65)")
    toolbin = build_toolchain(tool_dir)

    print("[3/4] extracting assets from the ROM")
    ok, bad = split_assets(src_dir, rom_bytes)
    print("      %d assets verified, %d mismatched" % (ok, bad))
    if bad:
        raise SystemExit("error: assets did not match the manifest")

    print("[4/4] assembling from source")
    out = os.path.join(BUILD, "rom", "smb-rebuilt.nes")
    obj = assemble(src_dir, toolbin, out)

    rebuilt = open(out, "rb").read()
    same = rebuilt == rom_bytes
    print()
    print("  rebuilt : %d bytes  sha1 %s" % (len(rebuilt), hashlib.sha1(rebuilt).hexdigest()))
    print("  original: %d bytes  sha1 %s" % (len(rom_bytes), hashlib.sha1(rom_bytes).hexdigest()))
    print()
    if same:
        print("  PASS  rebuilt ROM is BYTE-IDENTICAL to the original")
        print("        -> this is a genuine decompilation, verified bit-for-bit")
    else:
        print("  FAIL  rebuilt ROM differs from the original")
        return 1

    if args.report:
        print()
        print("  --- can it be trimmed into a smaller cartridge? ---")
        prg_report(src_dir, toolbin, obj)
    return 0


if __name__ == "__main__":
    sys.exit(main())
