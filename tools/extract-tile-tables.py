#!/usr/bin/env python3
"""Work out which CHR tiles the game's data tables point at.

Emulation can only prove a tile is used, never that it is unused. Bot runs are a
sample of the game, and the gaps are exactly the interesting objects: the fire
flower only exists if the player is big when a block is hit, fire Mario needs a
mushroom first, and the flagpole needs the end of the level. Every one of those
was blanked at some point because a run never happened to produce it.

The game's own tables are a complete answer, though, because the renderer only
ever draws a tile index it read out of one of them. This script extracts those
tables from the assembled ROM using the linker's label addresses, so the used
set is derived from the data rather than from luck.

Usage:
    python3 tools/extract-tile-tables.py <rom> <labels.txt> [--json out.json]

The ROM must be the unblanked reference for the labels to line up.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

# Tiles that are not graphics: $fc is the "no sprite" marker the OAM staging
# code uses, and $ff marks an empty sprite slot.
NON_TILE = {0xFC, 0xFD, 0xFE, 0xFF}


def load_labels(path):
    """Read an `ld65 -Ln` label file into {name: address}."""
    labels = {}
    with open(path, errors="replace") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 3 and parts[0] == "al":
                try:
                    labels[parts[2].lstrip(".")] = int(parts[1], 16)
                except ValueError:
                    pass
    return labels


def prg_offset(addr, prg_start=0x8000):
    """Map a CPU address in the PRG window to an offset in the ROM file."""
    return addr - prg_start


# Enemy ids world 1-1's stream actually spawns, decoded from the level data by
# tools/decode-area-enemies.py. Restricting the enemy art to these keeps whole
# enemies of unused worlds out of the protected set, which is worth a few hundred
# packed bytes, without guessing: anything not listed here cannot appear in 1-1.
AREA_1_1_ENEMY_IDS = [
    0x05, 0x06, 0x08, 0x16, 0x18, 0x1B, 0x24, 0x28, 0x29, 0x3B,
]


# Tables whose bytes are CHR tile indices. `length` is how many entries to trust;
# for the player table the exact split is in the source comments, but every byte
# in it is a tile index or the $fc marker, so the whole block is safe.
TILE_TABLES = {
    "tbl_player_graphics_tiles": 208,
    # 276 entries: 46 enemy/animation frames of six tiles each. A shorter read
    # silently loses whole enemies -- at 96 entries the spiny's tiles were missed
    # and got blanked, which is a very visible bug.
    "tbl_enemy_graphics_tiles": 276,
    "tbl_background_scenery_patterns": 144,
}


def enemy_tiles_for_area(rom, labels, ids):
    """Sprite tiles for the enemies a specific area can spawn.

    The frames an enemy can display come from tbl_enemy_graphics_tiles at the
    offset tbl_enemy_graphics_offsets gives for that id. Three consecutive frames
    per enemy is what the renderer walks through, so all three are collected.
    """
    tiles_addr = labels.get("tbl_enemy_graphics_tiles")
    offsets_addr = labels.get("tbl_enemy_graphics_offsets")
    if tiles_addr is None or offsets_addr is None:
        return set()

    tiles_off = prg_offset(tiles_addr)
    offsets_off = prg_offset(offsets_addr)

    out = set()
    for enemy_id in ids:
        frame_base = rom[offsets_off + enemy_id]
        for frame in range(3):
            start = tiles_off + frame_base + frame * 6
            for b in rom[start:start + 6]:
                if b not in NON_TILE:
                    out.add(b)
    return out


def extract(rom, labels):
    """Collect tile indices from every known table, plus the title read-back."""
    bg = set()
    sp = set()
    found = {}

    for name, length in TILE_TABLES.items():
        addr = labels.get(name)
        if addr is None:
            continue
        off = prg_offset(addr)
        data = rom[off:off + length]
        if len(data) != length:
            continue
        tiles = {b for b in data if b not in NON_TILE}
        found[name] = sorted(tiles)
        # The player and enemy tables are sprite art; scenery is background.
        if "scenery" in name:
            bg |= tiles
        elif name == "tbl_enemy_graphics_tiles":
            # The full table covers all 46 enemies in the game; the per-area set
            # below decides which of them this cut can actually spawn.
            found[name] = sorted(tiles)
        else:
            sp |= tiles

    area_tiles = enemy_tiles_for_area(rom, labels, AREA_1_1_ENEMY_IDS)
    found["area_1_1_enemies"] = sorted(area_tiles)
    sp |= area_tiles

    return bg, sp, found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rom")
    ap.add_argument("labels")
    ap.add_argument("--json")
    args = ap.parse_args()

    rom = open(args.rom, "rb").read()
    labels = load_labels(args.labels)

    bg, sp, found = extract(rom, labels)
    for name, tiles in found.items():
        print("%-34s %3d tiles" % (name, len(tiles)))
    print("%-34s %3d tiles" % ("union: background", len(bg)))
    print("%-34s %3d tiles" % ("union: sprites", len(sp)))

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"bg": sorted(bg), "sp": sorted(sp), "tables": found}, f, indent=1)
        print("wrote", args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
