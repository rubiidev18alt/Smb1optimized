#!/usr/bin/env python3
"""
make-cut-rom.py -- build a playable "World 1-1 only" cut of Super Mario Bros.

Cuts 1-2 through 8-4 by replacing their level and enemy streams with the
terminator byte, so the level parser ends those areas immediately. Everything
needed for 1-1 stays: full engine, sprites, Goombas/Koopas, music and sound
effects, title screen.

Usage
-----
  ./make-cut-rom.py -o dist/smb-1-1-only.nes
  ./make-cut-rom.py --no-verify

Requires tools/build-from-source.py to have run once (for source + toolchain).

What this does NOT do
---------------------
It does not shrink the file. PRG drops from 32762 to ~28499 bytes, but a 32 KiB
cartridge is quantised to 16 KiB banks, so the ROM remains 40976 bytes until
another ~10.6 KiB comes out of the 6502 engine. See FINDINGS.md.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
SRC = os.path.join(BUILD, "src", "smb1_src")
TOOLBIN = os.path.join(BUILD, "cc65", "bin")

# 1-1 is ground area 6 in this disassembly's numbering.
KEEP_AREA = "off_ground_area_6_objects"
KEEP_ENEMIES = "off_ground_area_6_enemies"
AREA_ASSET = "con_course_area_streams_asset"
ENEMY_ASSET = "con_course_enemy_streams_asset"


def check_prereqs():
    if not os.path.isdir(SRC):
        raise SystemExit("error: source not present. Run tools/build-from-source.py first.")
    for tool in ("ca65", "ld65"):
        if not os.path.exists(os.path.join(TOOLBIN, tool)):
            raise SystemExit("error: %s missing. Run tools/build-from-source.py first." % tool)


def stage_source(workdir):
    """Copy the pristine source so cuts never accumulate between runs."""
    if os.path.isdir(workdir):
        shutil.rmtree(workdir)
    shutil.copytree(os.path.join(SRC, "src"), os.path.join(workdir, "src"))
    return os.path.join(workdir, "src")


def cut_levels(src):
    """Point every non-1-1 area/enemy stream at a single terminator byte."""
    total_cut = 0

    def patch(path, suffix, asset, keep):
        nonlocal total_cut
        full = os.path.join(src, path)
        text = open(full).read()
        pattern = re.compile(
            r"(off_\w+_%s):\n\s*\.incbin %s, (\$[0-9a-f]+), (\$[0-9a-f]+)"
            % (suffix, asset))

        def repl(m):
            nonlocal total_cut
            label, off, length = m.group(1), m.group(2), m.group(3)
            if label == keep:
                return m.group(0)
            total_cut += max(0, int(length.lstrip("$"), 16) - 1)
            return "%s:\n    .incbin %s, $000, $001" % (label, asset)

        out = pattern.sub(repl, text)
        open(full, "w").write(out)
        return len(pattern.findall(text))

    n_areas = patch("data/levels/areas.asm", "objects", AREA_ASSET, KEEP_AREA)
    n_enemies = patch("data/levels/index_and_enemies.asm", "enemies", ENEMY_ASSET, KEEP_ENEMIES)
    return n_areas, n_enemies, total_cut


def assemble(src, out_path):
    env = dict(os.environ)
    env["PATH"] = TOOLBIN + os.pathsep + env.get("PATH", "")
    obj = os.path.join(BUILD, "rom", "cut.o")
    mapfile = os.path.join(BUILD, "rom", "cut.map")
    os.makedirs(os.path.dirname(obj), exist_ok=True)

    subprocess.run([os.path.join(TOOLBIN, "ca65"),
                    "-I", "src", "-I", "src/revisions/",
                    "--bin-include-dir", "assets/generated",
                    "-o", obj, "src/revisions/ju.asm"],
                   cwd=SRC, env=env, check=True, capture_output=True, text=True)
    subprocess.run([os.path.join(TOOLBIN, "ld65"),
                    "-C", "config/linker/nrom256.cfg",
                    "-m", mapfile, "-o", out_path, obj],
                   cwd=SRC, env=env, check=True, capture_output=True, text=True)
    return mapfile


def prg_size(mapfile):
    for line in open(mapfile):
        m = re.match(r"PRG\s+[0-9A-F]+\s+[0-9A-F]+\s+([0-9A-F]+)", line)
        if m:
            return int(m.group(1), 16)
    return None


def verify(rom):
    """Confirm the cut still boots to the title screen and draws World 1-1."""
    script = os.path.join(ROOT, "tools", "smoke-test.js")
    env = dict(os.environ)
    env["ROM"] = rom
    proc = subprocess.run(["node", script, "300"], cwd=ROOT, env=env,
                          capture_output=True, text=True)
    return proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--output",
                    default=os.path.join(ROOT, "dist", "smb-1-1-only.nes"))
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args(argv)

    check_prereqs()

    work = os.path.join(BUILD, "cut")
    print("[1/4] staging source")
    src = stage_source(work)

    print("[2/4] cutting 1-2 through 8-4")
    n_areas, n_enemies, cut_bytes = cut_levels(src)
    print("      %d area streams and %d enemy streams replaced with a terminator"
          % (n_areas - 1, n_enemies - 1))
    print("      %d bytes of level/enemy data removed" % cut_bytes)

    print("[3/4] assembling")
    out_path = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    mapfile = assemble(src, out_path)
    size = os.path.getsize(out_path)
    prg = prg_size(mapfile)
    print("      %s  %d bytes on disk, PRG %d bytes" % (out_path, size, prg))

    print("[4/4] verifying")
    if args.no_verify:
        print("      skipped")
        return 0
    ok, log = verify(out_path)
    tail = [l for l in log.splitlines() if l.strip()][-4:]
    for line in tail:
        print("      " + line.strip())
    if not ok:
        print("\n  FAIL: the cut ROM did not boot/play correctly", file=sys.stderr)
        return 1

    print()
    print("  PASS  playable cut: title screen, START, World 1-1 with enemies")
    print()
    print("  note: file size is unchanged (%d bytes). PRG dropped to %d bytes, but"
          % (size, prg))
    print("        a cartridge is quantised to 16 KiB PRG banks, and %d is still"
          % prg)
    print("        %d bytes over one bank. The engine itself must shrink for the"
          % (prg - 16384))
    print("        file to get smaller. See FINDINGS.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
