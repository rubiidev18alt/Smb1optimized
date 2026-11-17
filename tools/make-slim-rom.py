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
import json
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

ALL_CUTS = ("levels", "music", "gameover", "restart", "enemies", "enemyart", "title")
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
    """Boot straight into World 1-1, skipping the title screen and its demo.

    The title handler does the VRAM/nametable clearing, so simply forcing mode 1
    drew garbage metatiles. Instead, boot into game mode at task 1: task 0 is
    handler_initialize_area (level header, pointers) and task 2 is
    handler_run_screen_task, whose first step clears both nametables. Booting at
    task 1 keeps that ordering and skips only the title-specific work.
    """
    boot = os.path.join(src, "system", "boot_and_frame.asm")
    def force_game(t):
        # Task must stay 0: in game mode task 0 is handler_initialize_area
        # (level header + palette), task 1 clears VRAM, task 2 sets up, task 3
        # runs the game. Setting task 1 here skipped area init and drew garbage.
        return t.replace(
            "    STA ram_oper_mode  ; reset primary mode of operation",
            "    LDA #1\n"
            "    STA ram_oper_mode  ; title screen removed: boot into gameplay",
            1)
    if not _patch_file(boot, force_game):
        raise SystemExit("error: could not find the boot mode store")

    # The title screen handler is now unreachable; stub it so its code and the
    # demo engine can be dropped.
    for label in ("handler_run_title_screen_mode",):
        for root, _d, files in os.walk(src):
            for name in files:
                if not name.endswith(".asm"):
                    continue
                path = os.path.join(root, name)
                if label + ":" in open(path, errors="replace").read():
                    _stub_label(path, label)
                    return


def cut_gameover(src):
    """Completing a level triggers the game-over sequence.

    Advancing to the next world would load an area that no longer exists, which
    is the garbage level / black screen. Game over is a real, tested path: it
    shows the game-over screen, then returns to the title screen.
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
        # 1-2 onward is removed, so finishing 1-1 ends the run instead of
        # loading an area that is no longer there.
        replacement = ("    LDA #con_mode_game_over\n"
                       "    STA ram_oper_mode\n"
                       "    LDA #$00\n"
                       "    STA ram_oper_mode_task")
        return t.replace(target, replacement, 1)
    _patch_file(modes, transform)



# Enemy/object handlers that World 1-1 never reaches. 1-1 spawns Goombas and a
# Koopa only (object ids $00-$14, the "normal enemy" path), so everything for
# Bowser, firebars, platforms, vines, fireworks, jumpsprings and warp zones is
# unreachable.
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


# Enemy sprite rows reachable in World 1-1.
KEEP_TILE_ROWS = (2, 3, 14, 15, 16, 17, 18, 23)


def cut_enemy_art(src):
    """Shrink tbl_enemy_graphics_tiles to the frames 1-1 can reach."""
    path = os.path.join(src, "rendering", "actors", "enemies.asm")
    text = open(path, errors="replace").read()

    m = re.search(r"tbl_enemy_graphics_tiles:\n(.*?)\n\n(tbl_enemy_graphics_offsets:)",
                  text, re.S)
    if not m:
        raise SystemExit("error: enemy graphics tables not found")

    rows = [line for line in m.group(1).splitlines()
            if ".byte" in line and not line.strip().startswith(";")]
    if len(rows) < 24:
        raise SystemExit("error: unexpected enemy tile table shape (%d rows)" % len(rows))

    keep = [rows[i] for i in KEEP_TILE_ROWS]
    new_tiles = "tbl_enemy_graphics_tiles:\n" + "\n".join(keep) + "\n"

    goomba_off = KEEP_TILE_ROWS.index(14) * 6
    new_offsets = []
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

    start_off = m.end(1) + 2
    om = re.search(r"tbl_enemy_graphics_offsets:\n(.*?)\n\n", text[m.end(1):], re.S)
    end_off = start_off + len(om.group(0))
    rebuilt = (text[:m.start()] + new_tiles + "\n" + new_off_block + text[end_off:])
    open(path, "w").write(rebuilt)



def cut_restart(src):
    """Finishing a level restarts the run instead of loading the next one."""
    modes = os.path.join(src, "game", "modes.asm")
    def transform(t):
        target = ("    INC ram_world_number  ; increment world number to move onto the next world\n"
                  "    JSR sub_load_area_pointer  ; get area address offset for the next area\n"
                  "    INC ram_fetch_new_game_timer_flag  ; set flag to load game timer from header\n"
                  "    LDA #con_mode_game\n"
                  "    STA ram_oper_mode  ; set mode of operation to game mode")
        if target not in t:
            raise SystemExit("error: could not find handler_finish_world advance block")
        replacement = ("    LDA #$00\n"
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


CUT_FUNCS = {"levels": cut_levels, "music": cut_music, "enemies": cut_enemies,
             "enemyart": cut_enemy_art,
             "title": cut_title, "restart": cut_restart, "gameover": cut_gameover}


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


def harvest_chr_usage(rom):
    """Run the ROM and report which CHR tile indices it displays."""
    out = os.path.join(BUILD, "rom", "chr_usage.json")
    p = subprocess.run(["node", os.path.join(ROOT, "tools", "find-unused-chr.js"),
                        rom, "6000"],
                       cwd=ROOT, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit("error: CHR usage harvest failed:\n" + (p.stdout or "") + (p.stderr or ""))
    open(out, "w").write(p.stdout)
    return json.loads(p.stdout)


# CHR is laid out as two 256-tile pattern tables. SMB renders the background
# from table 1 ($1000) and sprites from table 0 ($0000); swapping that mapping
# silently erases the terrain, so the halves are named rather than indexed.
CHR_OFFSET = 16 + 32768
CHR_BG_BASE = CHR_OFFSET + 256 * 16
CHR_SP_BASE = CHR_OFFSET


def blank_unused_chr(rom_bytes, usage):
    """Zero the 16 bytes of every CHR tile the game never displays.

    The PPU addresses tiles by index, so the index must stay valid -- only the
    contents are blanked. Unused tiles then collapse to runs of zeros, which is
    what makes the CHR dramatically more compressible.
    """
    bg = set(usage["bg"])
    sp = set(usage["sp"])
    out = bytearray(rom_bytes)
    blanked = 0
    for i in range(256):
        if i not in bg:
            off = CHR_BG_BASE + i * 16
            out[off:off + 16] = b"\x00" * 16
            blanked += 1
        if i not in sp:
            off = CHR_SP_BASE + i * 16
            out[off:off + 16] = b"\x00" * 16
            blanked += 1
    return bytes(out), blanked


def _tile_slot(table, index):
    base = CHR_BG_BASE if table == "bg" else CHR_SP_BASE
    return base + index * 16


def probe_chr(rom, quick=False):
    """Fingerprint the screens a ROM renders, for before/after comparison."""
    cmd = ["node", os.path.join(ROOT, "tools", "chr-probe.js"), rom]
    if quick:
        cmd.append("--quick")
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit("error: CHR probe failed:\n" + (p.stdout or "") + (p.stderr or ""))
    return json.loads(p.stdout)


def _probe_equivalent(a, b):
    """True when two probe results describe the same visible behaviour."""
    keys = set(a) & set(b)
    keys.discard("rom")
    return all(a.get(k) == b.get(k) for k in keys)


# The title screen is not stored as graphics: `handler_copy_title_screen_from_chr`
# copies the raw bytes of background tiles 236-255 through $2007 into RAM and
# decodes them as the title logo. Blanking any of those tiles therefore deletes
# part of the logo even though the tiles never appear in a nametable. They are
# listed here explicitly rather than discovered at runtime so the reason is
# recorded next to the rule.
CHR_NEVER_BLANK_BG = frozenset(range(236, 256))


def find_drawn_but_blank(rom):
    """Return the tiles the ROM draws even though their CHR bytes are blank.

    Sampling nametables and OAM to guess what is unused misses tiles that only
    appear briefly or at one scroll position. Running the blanked ROM and
    looking for the failure directly finds them: a tile index that is on screen
    while its 16 bytes are zero is a tile that has to be put back. The scan
    shares its input timeline with the acceptance probe, so it sees exactly the
    screens the probe fingerprints.
    """
    p = subprocess.run(["node", os.path.join(ROOT, "tools", "chr-probe.js"), rom, "--dead"],
                       cwd=ROOT, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit("error: dead-tile scan failed:\n" + (p.stdout or "") + (p.stderr or ""))
    return json.loads(p.stdout).get("dead", {})


def blank_unused_chr_verified(reference_rom, working_rom, usage):
    """Blank the tiles the game never shows, then repair anything it still draws.

    The pattern table is read back by the game in one place -- the title screen
    is rebuilt from the raw bytes of tiles 236-255 -- so those are protected up
    front. Everything else is blanked optimistically and any tile that turns up
    on screen while blank is restored. The loop runs until a full pass finds
    nothing drawn-and-blank, then the rendered screens are compared against the
    unblanked reference as a final gate.
    """
    ref = open(reference_rom, "rb").read()
    work = bytearray(working_rom)

    keep_bg = set(usage["bg"]) | CHR_NEVER_BLANK_BG
    keep_sp = set(usage["sp"])
    for i in range(256):
        if i not in keep_bg:
            off = CHR_BG_BASE + i * 16
            work[off:off + 16] = b"\x00" * 16
        if i not in keep_sp:
            off = CHR_SP_BASE + i * 16
            work[off:off + 16] = b"\x00" * 16

    restored = []
    scratch = os.path.join(BUILD, "rom", "chr_probe.nes")
    for _ in range(8):
        with open(scratch, "wb") as f:
            f.write(work)
        dead = find_drawn_but_blank(scratch)
        fixes = []
        for table in ("bg", "sp"):
            for i in dead.get(table, []):
                # A tile that is empty in the original ROM is not a lost tile:
                # restoring it would only cost bytes. Skip those.
                off = _tile_slot(table, i)
                if ref[off:off + 16] == b"\x00" * 16:
                    continue
                if (table, i) not in restored:
                    fixes.append((table, i))
        if not fixes:
            break
        for table, i in fixes:
            off = _tile_slot(table, i)
            work[off:off + 16] = ref[off:off + 16]
            restored.append((table, i))

    # Any tile still reported dead at this point was already empty in the
    # reference ROM, so it is not missing art -- SMB1 legitimately draws two
    # empty tiles (the blank cell the screen is cleared with, and an empty 8x8
    # in Mario's sprite set). Report them so the output is not mistaken for an
    # unresolved repair.
    with open(scratch, "wb") as f:
        f.write(work)
    leftover = find_drawn_but_blank(scratch)
    ref_dead = find_drawn_but_blank(reference_rom)
    leftover = {k: [t for t in v if t not in set(ref_dead.get(k, []))]
                for k, v in leftover.items()}

    # Final gate: the blanked build must render the same screens as the
    # reference. This is what catches anything the loop above cannot see.
    with open(scratch, "wb") as f:
        f.write(work)
    if not _probe_equivalent(probe_chr(reference_rom), probe_chr(scratch)):
        raise SystemExit(
            "error: blanked CHR does not render identically to the unblanked build;"
            " refusing to emit a ROM that looks different")

    blanked = 512 - len(keep_bg) - len(keep_sp) - len(
        [r for r in restored if (r[0] == "bg" and r[1] not in keep_bg)
         or (r[0] == "sp" and r[1] not in keep_sp)])
    return bytes(work), blanked, restored, leftover


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
    ap.add_argument("--no-chr", action="store_true",
                    help="skip the unused-CHR blanking pass")
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

    # CHR pass: find which tiles the game displays, blank the rest, and save the
    # ROM again. Repeats until the used-tile set stops changing.
    chr_saved = 0
    if not args.no_chr:
        print()
        print("CHR pass")
        # Harvest ONCE from the unblanked ROM: blanking tiles and then
        # re-harvesting makes the game render nothing for them, which looks
        # "unused" and cascades into blanking everything.
        # Keep an unblanked copy: it is the reference the blanked build has to
        # stay pixel-identical to. PRG is untouched by the CHR pass, so this is
        # exactly the same program with a complete pattern table.
        reference = os.path.join(BUILD, "rom", "chr_reference.nes")
        shutil.copyfile(out_path, reference)

        usage = harvest_chr_usage(reference)
        blanked_rom, n, restored, leftover = blank_unused_chr_verified(
            reference, open(reference, "rb").read(), usage)
        with open(out_path, "wb") as f:
            f.write(blanked_rom)
        chr_saved = n
        print("  blanked %d tiles (%d bg + %d sp visible)"
              % (n, len(usage["bg"]), len(usage["sp"])))
        if restored:
            print("  restored %d load-bearing tile(s) to keep rendering identical: %s"
                  % (len(restored), ", ".join("%s#%d" % t for t in restored)))
        empties = [(tbl, t) for tbl, ts in sorted(leftover.items()) for t in ts]
        if empties:
            print("  %d tile(s) still empty on screen, also empty in the original"
                  " (legitimate blank cells): %s"
                  % (len(empties), ", ".join("%s#%d" % t for t in empties)))
        # A blanked ROM is no longer byte-identical to the original by design.
        size = os.path.getsize(out_path)
        packed = len(__import__("lzma").compress(
            open(out_path, "rb").read(),
            preset=9 | __import__("lzma").PRESET_EXTREME))
        orig_packed = len(__import__("lzma").compress(
            open(os.path.join(ROOT, "Super Mario Bros. (JU) (PRG0) [!].nes"), "rb").read(),
            preset=9 | __import__("lzma").PRESET_EXTREME))
        print("  compressed: %d bytes (%.1f%% smaller than the original's %d)"
              % (packed, 100.0 - 100.0 * packed / orig_packed, orig_packed))
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
