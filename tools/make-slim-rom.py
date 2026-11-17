#!/usr/bin/env python3
"""
make-slim-rom.py -- build a stripped-down, 1-1-only Super Mario Bros.

Applies a stack of individually-measured cuts to the decompiled source and
reports the PRG footprint after each, so the cost of every feature is visible.

Cuts
----
  levels   remove 1-2 through 8-4 (area + enemy streams -> terminator)
  music    stub the audio engine (no music, no sound effects)
  title    boot straight into 1-1, no title screen
  restart  finishing a level restarts the game instead of loading the next one

Usage
-----
  ./make-slim-rom.py                        # all cuts, write dist/smb-slim.nes
  ./make-slim-rom.py --only levels,music    # measure a subset
  ./make-slim-rom.py --keep title           # all cuts except title
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
PRG_BASE = 0x8000

ALL_CUTS = ("levels", "music", "restart", "enemies", "enemyart", "title")
KEEP_AREA = "off_ground_area_6_objects"      # 1-1
KEEP_ENEMIES = "off_ground_area_6_enemies"
AREA_ASSET = "con_course_area_streams_asset"
ENEMY_ASSET = "con_course_enemy_streams_asset"


def check_prereqs():
    if not os.path.isdir(SRC):
        raise SystemExit("error: run tools/build-from-source.py first")
    for t in ("ca65", "ld65"):
        if not os.path.exists(os.path.join(TOOLBIN, t)):
            raise SystemExit("error: %s missing; run tools/build-from-source.py" % t)


def stage(work):
    """Copy the whole source tree so cuts apply to a scratch copy, never the original."""
    if os.path.isdir(work):
        shutil.rmtree(work)
    shutil.copytree(SRC, work, ignore=shutil.ignore_patterns(".git", "build"))
    return os.path.join(work, "src")


def _patch_file(path, transform):
    text = open(path).read()
    out = transform(text)
    if out != text:
        open(path, "w").write(out)
        return True
    return False


# --------------------------------------------------------------------------- #
# individual cuts
# --------------------------------------------------------------------------- #

def cut_levels(src):
    """Point every non-1-1 area/enemy stream at the single terminator byte."""
    def patch(path, suffix, asset, keep):
        pattern = re.compile(
            r"(off_\w+_%s):\n\s*\.incbin %s, (\$[0-9a-f]+), (\$[0-9a-f]+)"
            % (suffix, asset))

        def transform(text):
            def repl(m):
                if m.group(1) == keep:
                    return m.group(0)
                return "%s:\n    .incbin %s, $000, $001" % (m.group(1), asset)
            return pattern.sub(repl, text)

        _patch_file(os.path.join(src, path), transform)

    patch("data/levels/areas.asm", "objects", AREA_ASSET, KEEP_AREA)
    patch("data/levels/index_and_enemies.asm", "enemies", ENEMY_ASSET, KEEP_ENEMIES)


def cut_music(src):
    """Replace sub_sound_engine with an immediate RTS and drop its body.

    The body is wrapped in `.if 0` rather than deleted so that any nested
    `.if/.endif` pairs stay balanced -- a naive text cut orphans an `.endif`.
    The subroutine's extent is found by scanning to the next top-level label.
    """
    path = os.path.join(src, "audio", "sound_effects.asm")
    lines = open(path).read().splitlines(keepends=True)

    start = None
    for i, line in enumerate(lines):
        if line.startswith("sub_sound_engine:"):
            start = i
            break
    if start is None:
        raise SystemExit("error: sub_sound_engine not found")

    end = None
    for j in range(start + 1, len(lines)):
        if re.match(r"^[A-Za-z_.][A-Za-z0-9_.]*:\s*$", lines[j]):
            end = j
            break
    if end is None:
        raise SystemExit("error: could not find the end of sub_sound_engine")

    body = lines[start + 1:end]
    replaced = ([lines[start],
                 "    RTS  ; audio removed: no music, no sound effects\n",
                 "    .if 0  ; dead body retained only to keep .if/.endif balanced\n"]
                + body
                + ["    .endif\n"])
    open(path, "w").write("".join(lines[:start] + replaced + lines[end:]))

    # Drop the music stream data itself: with the engine stubbed nothing reads
    # it, and it is the contiguous block at the top of PRG ($E95A-$EF53).
    streams = os.path.join(src, "audio", "music_streams.asm")
    def drop_streams(t):
        return re.sub(
            r"(\.incbin con_music_data_asset, [^\n]+)",
            "; music data removed", t)
    _patch_file(streams, drop_streams)

    # Keep the APU silent at boot so nothing is left ringing.
    boot = os.path.join(src, "system", "boot_and_frame.asm")
    def silence(t):
        return t.replace(
            "    LDA #%00001111\n    STA SND_MASTERCTRL_REG",
            "    LDA #%00000000\n    STA SND_MASTERCTRL_REG  ; audio removed: APU silent",
            1)
    _patch_file(boot, silence)


def cut_title(src):
    """Boot straight into gameplay instead of the title screen.

    mode 0 is the title screen; forcing mode 1 at boot skips it entirely.
    """
    boot = os.path.join(src, "system", "boot_and_frame.asm")
    def force_game(t):
        # sub_initialize_memory leaves A = 0; mode 0 = title. Put the game first.
        return t.replace(
            "    STA ram_oper_mode  ; reset primary mode of operation",
            "    LDA #1\n    STA ram_oper_mode  ; title screen removed: boot into gameplay",
            1)
    if not _patch_file(boot, force_game):
        # fall back to the other boot path
        _patch_file(boot, lambda t: t.replace(
            "    STA ram_oper_mode\n    STA ram_fds_disk_loader_task",
            "    LDA #1\n    STA ram_oper_mode  ; title screen removed\n    STA ram_fds_disk_loader_task", 1))


def cut_restart(src):
    """Finishing a level restarts the run instead of loading the next one.

    handler_finish_world advances to the next world; with only 1-1 present that
    loads a removed area and shows a black screen. Force a soft restart instead.
    """
    modes = os.path.join(src, "game", "modes.asm")
    def transform(t):
        target = ("    INC ram_world_number  ; increment world number to move onto the next world\n"
                  "    JSR sub_load_area_pointer  ; get area address offset for the next area\n"
                  "    INC ram_fetch_new_game_timer_flag  ; set flag to load game timer from header\n"
                  "    LDA #con_mode_game\n"
                  "    STA ram_oper_mode  ; set mode of operation to game mode")
        if target not in t:
            raise SystemExit("error: could not find handler_finish_world advance block")
        replacement = ("    ; restart the run: only 1-1 exists, so advancing would\n"
                       "    ; load a removed area and hang on a black screen.\n"
                       "    LDA #$00\n"
                       "    STA ram_world_number\n"
                       "    STA ram_area_number\n"
                       "    STA ram_level_number\n"
                       "    STA ram_oper_mode_task\n"
                       "    JSR sub_load_area_pointer\n"
                       "    INC ram_fetch_new_game_timer_flag\n"
                       "    LDA #con_mode_game\n"
                       "    STA ram_oper_mode")
        return t.replace(target, replacement, 1)
    _patch_file(modes, transform)



# Enemy/object handlers that World 1-1 never reaches. 1-1 spawns Goombas and a
# Koopa only (object ids $00-$14, the "normal enemy" path), so everything for
# Bowser, firebars, platforms, vines, fireworks, jumpsprings and warp zones is
# unreachable. Each is replaced by an immediate RTS; the body is kept inside
# `.if 0` so nested .if/.endif pairs stay balanced.
UNUSED_HANDLERS = [
    "handler_run_bowser_flame",
    "handler_run_fireworks",
    "handler_run_firebar",
    "handler_run_large_platform",
    "handler_run_small_platform",
    "handler_run_bowser",
    "handler_run_vine_object",
    "handler_run_star_flag",
    "handler_process_jumpspring",
    "handler_run_warp_zone_object",
    "handler_move_hammer_bro",
    "sub_draw_vine",
    "sub_draw_explosion_fireworks",
    "sub_draw_small_platform",
]


def _stub_label(path, label):
    """Replace a routine body with RTS, keeping the label address valid."""
    lines = open(path, errors="replace").read().splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if line.startswith(label + ":"):
            start = i
            break
    if start is None:
        return False
    end = None
    for j in range(start + 1, len(lines)):
        if re.match(r"^[A-Za-z_.][A-Za-z0-9_.]*:\s*$", lines[j]):
            end = j
            break
    if end is None:
        return False
    body = lines[start + 1:end]
    replaced = ([lines[start],
                 "    RTS  ; unreachable in the 1-1 cut\n",
                 "    .if 0\n"] + body + ["    .endif\n"])
    open(path, "w").write("".join(lines[:start] + replaced + lines[end:]))
    return True


def cut_enemies(src):
    """Stub the enemy/object handlers that World 1-1 cannot reach."""
    targets = list(UNUSED_HANDLERS)
    stubbed = 0
    for root, _dirs, files in os.walk(src):
        for name in files:
            if not name.endswith(".asm"):
                continue
            path = os.path.join(root, name)
            text = open(path, errors="replace").read()
            for label in list(targets):
                if label + ":" in text:
                    if _stub_label(path, label):
                        stubbed += 1
                        targets.remove(label)
    if not stubbed:
        raise SystemExit("error: no unused enemy handlers were found to stub")
    return stubbed



# Enemy sprite rows reachable in World 1-1: koopa (2,3), goomba (14),
# shells (15-18), defeated goomba (23). Everything else in the flat tile table
# is for types 1-1 never spawns, so those rows are dropped and their offsets
# re-pointed at the goomba row.
KEEP_TILE_ROWS = (2, 3, 14, 15, 16, 17, 18, 23)


def cut_enemy_art(src):
    """Shrink tbl_enemy_graphics_tiles to the frames 1-1 can reach."""
    path = os.path.join(src, "rendering", "actors", "enemies.asm")
    text = open(path, errors="replace").read()

    m = re.search(r"tbl_enemy_graphics_tiles:\n(.*?)\n\n(tbl_enemy_graphics_offsets:)",
                  text, re.S)
    if not m:
        raise SystemExit("error: enemy graphics tables not found")

    rows = []
    for line in m.group(1).splitlines():
        if ".byte" in line and not line.strip().startswith(";"):
            rows.append(line)
    if len(rows) < 24:
        raise SystemExit("error: unexpected enemy tile table shape (%d rows)" % len(rows))

    # Rebuild: only the rows 1-1 uses, in original order, each 6 bytes.
    keep = [rows[i] for i in KEEP_TILE_ROWS]
    new_tiles = "tbl_enemy_graphics_tiles:\n" + "\n".join(keep) + "\n"

    # Re-point the offsets: kept rows get sequential offsets, others alias goomba.
    goomba_off = KEEP_TILE_ROWS.index(14) * 6
    om = re.search(r"tbl_enemy_graphics_offsets:\n(.*?)\n\n", text[m.end(1):], re.S)
    new_offsets = []
    idx = 0
    for i in range(47):
        if i in KEEP_TILE_ROWS:
            new_offsets.append(KEEP_TILE_ROWS.index(i) * 6)
        else:
            new_offsets.append(goomba_off)
    off_lines = []
    for i in range(0, len(new_offsets), 8):
        chunk = ", ".join("$%02x" % v for v in new_offsets[i:i + 8])
        off_lines.append("    .byte " + chunk)
    new_off_block = "tbl_enemy_graphics_offsets:\n" + "\n".join(off_lines) + "\n"

    # Replace both blocks in one pass.
    start_off = m.end(1) + 2
    end_off = start_off + len(om.group(0))
    rebuilt = (text[:m.start()] + new_tiles + "\n" + new_off_block
               + text[end_off:])
    open(path, "w").write(rebuilt)


CUT_FUNCS = {"levels": cut_levels, "music": cut_music, "enemies": cut_enemies,
             "enemyart": cut_enemy_art,
             "title": cut_title, "restart": cut_restart}


# --------------------------------------------------------------------------- #
# build + measure
# --------------------------------------------------------------------------- #

def assemble(stage_root, out_path, mapfile):
    """Assemble inside a staged tree (which holds src/, config/ and assets/)."""
    env = dict(os.environ)
    env["PATH"] = TOOLBIN + os.pathsep + env.get("PATH", "")
    obj = os.path.join(BUILD, "rom", "slim.o")
    os.makedirs(os.path.dirname(obj), exist_ok=True)
    r = subprocess.run([os.path.join(TOOLBIN, "ca65"),
                    "-I", "src", "-I", "src/revisions/",
                    "--bin-include-dir", "assets/generated",
                    "-o", obj, "src/revisions/ju.asm"],
                   cwd=stage_root, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("ca65 failed:\n" + (r.stdout or "") + (r.stderr or ""))
    l = subprocess.run([os.path.join(TOOLBIN, "ld65"),
                    "-C", "config/linker/nrom256.cfg",
                    "-m", mapfile, "-o", out_path, obj],
                   cwd=stage_root, env=env, capture_output=True, text=True)
    if l.returncode != 0:
        raise SystemExit("ld65 failed:\n" + (l.stdout or "") + (l.stderr or ""))
    return obj


def prg_used(mapfile):
    for line in open(mapfile, errors='replace'):
        m = re.match(r"PRG\s+([0-9A-F]+)\s+([0-9A-F]+)\s+([0-9A-F]+)", line)
        if m:
            return int(m.group(3), 16)
    raise SystemExit("error: could not read PRG size from the map")


def verify(rom):
    env = dict(os.environ)
    env["ROM"] = rom
    p = subprocess.run(["node", os.path.join(ROOT, "tools", "smoke-test.js"), "300"],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    return p.returncode == 0, (p.stdout or "") + (p.stderr or "")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--output", default=os.path.join(ROOT, "dist", "smb-slim.nes"))
    ap.add_argument("--only", help="comma-separated cuts to apply")
    ap.add_argument("--keep", help="comma-separated cuts to skip")
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args(argv)

    check_prereqs()

    cuts = list(ALL_CUTS)
    if args.only:
        cuts = [c.strip() for c in args.only.split(",") if c.strip()]
    if args.keep:
        skip = {c.strip() for c in args.keep.split(",")}
        cuts = [c for c in cuts if c not in skip]
    for c in cuts:
        if c not in CUT_FUNCS:
            raise SystemExit("error: unknown cut %r (choose from %s)"
                             % (c, ", ".join(ALL_CUTS)))

    work = os.path.join(BUILD, "slim")
    src = stage(work)
    root = work
    mapfile = os.path.join(BUILD, "rom", "slim.map")
    out_path = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    baseline = prg_used(_assemble_baseline(root, mapfile))
    print("PRG baseline (no cuts): %d bytes\n" % baseline)
    print("%-10s %10s %10s   %s" % ("cut", "PRG after", "saved", "cumulative"))
    print("-" * 62)

    prev = baseline
    applied = []
    for cut in cuts:
        CUT_FUNCS[cut](src)
        applied.append(cut)
        prg = prg_used(_assemble_baseline(root, mapfile))
        print("%-10s %10d %10d   -%d (%.1f%%)"
              % (cut, prg, prev - prg, baseline - prg, 100.0 * (baseline - prg) / baseline))
        prev = prg

    assemble(root, out_path, mapfile)
    prg = prg_used(mapfile)
    size = os.path.getsize(out_path)

    print()
    print("applied        : %s" % ", ".join(applied))
    print("PRG            : %d -> %d bytes (%.1f%% smaller)"
          % (baseline, prg, 100.0 * (baseline - prg) / baseline))
    print(".nes on disk   : %d bytes" % size)
    print("over 16K bank  : %d bytes" % max(0, prg - 0x4000))
    print()

    if args.no_verify:
        return 0
    ok, log = verify(out_path)
    for line in [l for l in log.splitlines() if l.strip()][-5:]:
        print("  " + line.strip())
    if not ok:
        print("\nFAIL: the slim ROM did not boot into gameplay", file=sys.stderr)
        return 1
    print("\nPASS  boots straight into World 1-1 and plays")
    return 0


def _assemble_baseline(root, mapfile):
    """Assemble to a scratch file purely to read the PRG footprint."""
    assemble(root, os.path.join(BUILD, "rom", "measure.nes"), mapfile)
    return mapfile


if __name__ == "__main__":
    sys.exit(main())
