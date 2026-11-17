#!/usr/bin/env python3
"""Report which enemy objects world 1-1 actually spawns.

The full enemy-art table covers all 46 enemies in the game. This cut only keeps
world 1-1, so most of that art is unreachable -- but "unreachable" has to be
proved, not assumed. Twice now a tile was blanked because a test run did not
happen to produce it: the spiny, and later the fire flower.

The level's enemy stream is a small byte stream, so it can be decoded exactly.
Whatever it names is what 1-1 can spawn, and that is the set of sprite art the
cut has to keep.

Usage: python3 tools/decode-area-enemies.py <enemy_streams.bin> [offset] [length]
"""

from __future__ import annotations

import sys

# Enemy ids that the spawn command can introduce, from memory/constants.inc.
ENEMY_NAMES = {
    0x00: "green koopa",
    0x02: "buzzy beetle",
    0x03: "red koopa",
    0x05: "hammer bro",
    0x06: "goomba",
    0x07: "bloober",
    0x08: "bullet bill",
    0x0A: "green cheep",
    0x0B: "red cheep",
    0x0C: "podoboo",
    0x0D: "piranha plant",
    0x0E: "green paratroopa (jump)",
    0x0F: "red paratroopa",
    0x10: "green paratroopa (fly)",
    0x11: "lakitu",
    0x12: "spiny",
    0x14: "flying cheep",
    0x16: "fireworks",
    0x18: "stop frenzy",
    0x1B: "firebar cw slow",
    0x1C: "firebar cw fast",
    0x1D: "firebar ccw slow",
    0x1E: "firebar ccw fast",
    0x1F: "firebar long",
    0x24: "balance lift",
    0x25: "vertical lift",
    0x26: "large lift up",
    0x27: "large lift down",
    0x28: "horizontal lift",
    0x29: "drop lift",
    0x2A: "right moving lift",
    0x2B: "small lift up",
    0x2C: "small lift down",
    0x2D: "bowser",
    0x2E: "power-up object",
    0x2F: "vine object",
    0x30: "flagpole flag",
    0x31: "star flag",
    0x32: "jumpspring",
    0x33: "bullet bill cannon",
    0x35: "retainer object",
    # Group spawns: d0 picks three instead of two, d1 puts them on the high row.
    0x37: "goomba pair (low)",
    0x38: "goomba trio (low)",
    0x39: "goomba pair (high)",
    0x3A: "goomba trio (high)",
    0x3B: "koopa pair (low)",
    0x3C: "koopa trio (low)",
    0x3D: "koopa pair (high)",
    0x3E: "koopa trio (high)",
}

# Group commands expand to real enemies, so the art for those is needed too.
GROUPS = {
    0x37: ["goomba"],
    0x38: ["goomba"],
    0x39: ["goomba"],
    0x3A: ["goomba"],
    0x3B: ["green koopa"],
    0x3C: ["green koopa"],
    0x3D: ["green koopa"],
    0x3E: ["green koopa"],
}


def decode(stream):
    """Walk the stream and return the (row, id) records it contains.

    Records are two bytes: a row/position byte and an enemy byte whose low six
    bits are the id. A row nybble of $0e marks a three-byte command; $0f selects
    a new page; $ff ends the stream.
    """
    records = []
    i = 0
    while i < len(stream):
        head = stream[i]
        if head == 0xFF:
            break
        row = (head >> 4) & 0x0F
        if row == 0x0E:
            if i + 2 >= len(stream):
                break
            enemy = stream[i + 2]
            i += 3
        elif row == 0x0F:
            # Page select: two bytes, no enemy of its own.
            i += 2
            continue
        else:
            if i + 1 >= len(stream):
                break
            enemy = stream[i + 1]
            i += 2
        if enemy == 0xFF:
            break
        records.append((row, enemy & 0x3F))
    return records


def main():
    path = sys.argv[1]
    offset = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x0E9
    length = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x025
    data = open(path, "rb").read()[offset:offset + length]

    records = decode(data)
    names = set()
    for _row, enemy in records:
        name = ENEMY_NAMES.get(enemy, "id $%02x" % enemy)
        names.add(name)
        for extra in GROUPS.get(enemy, []):
            names.add(extra)

    print("records: %d" % len(records))
    for name in sorted(names):
        print("  %s" % name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
