#!/usr/bin/env node
/*
 * chr-probe.js -- fingerprint what a ROM actually shows, frame by frame.
 *
 * Blanking unused CHR tiles is only safe if the game still renders identically.
 * Reading the nametable to decide what is "used" is not enough: some tiles are
 * load-bearing without ever being listed, and some are drawn at exactly one
 * scroll position. So this drives the ROM through the screens the cut build can
 * show, hashes the framebuffer at checkpoints, and on request reports every
 * tile that was drawn while its 16 CHR bytes were zero.
 *
 * The input sequence comes from rom-timeline.js, which the CHR repair loop in
 * make-slim-rom.py also uses, so "same screens" has a single definition.
 *
 * Usage: node tools/chr-probe.js <rom> [--quick] [--dead]
 *   --quick   title + start of gameplay only
 *   --dead    also report tiles drawn while blank
 * Output: JSON {title, level_start, level_walk, gameover, dead:{bg,sp}}
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const crypto = require("crypto");
const { MODE, plan, inputsFor, checkpoints, walkStartOf, walkEndOf } = require("./rom-timeline.js");

const ROOT = path.join(__dirname, "..");

function loadJsnes() {
  const file = path.join(ROOT, "node_modules", "jsnes", "dist", "jsnes.js");
  const sandbox = { exports: {}, module: { exports: {} } };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(file, "utf8"), sandbox, { filename: file });
  return sandbox.module.exports;
}

const jsnes = loadJsnes();
const romPath = process.argv[2];
const quick = process.argv.includes("--quick");
const wantDead = process.argv.includes("--dead");

if (!romPath) {
  console.error("usage: node tools/chr-probe.js <rom> [--quick] [--dead]");
  process.exit(2);
}

const rom = fs.readFileSync(romPath);

// CHR follows the 16-byte iNES header and the PRG ROM: two 256-tile pattern
// tables, background in table 1 and sprites in table 0.
const CHR_START = 16 + (rom[4] === 2 ? 32768 : rom[4] * 16384);
const BG_BASE = CHR_START + 256 * 16;
const SP_BASE = CHR_START;

function slotIsBlank(base, index) {
  const off = base + index * 16;
  for (let i = 0; i < 16; i++) if (rom[off + i] !== 0) return false;
  return true;
}

let last = null;
const nes = new jsnes.NES({
  onFrame: (buf) => {
    last = buf;
  },
  sampleRate: 48000,
  emulateSound: false,
});
nes.loadROM(new Uint8Array(rom));
nes.ppu.palTable.loadDefaultPalette();

const BUTTON = {};
for (const name of ["START", "RIGHT", "A", "B"]) BUTTON[name] = jsnes.Controller["BUTTON_" + name];

const deadBg = new Set();
const deadSp = new Set();
function scanDead() {
  if (!wantDead) return;
  const nt = nes.ppu.vramMem;
  if (nt) {
    for (let a = 0x2000; a < 0x2c00; a++) {
      const t = nt[a];
      if (!deadBg.has(t) && slotIsBlank(BG_BASE, t)) deadBg.add(t);
    }
  }
  const oam = nes.ppu.spriteMem;
  for (let s = 0; s < 64; s++) {
    if (oam[s * 4] >= 240) continue;
    const t = oam[s * 4 + 1];
    if (!deadSp.has(t) && slotIsBlank(SP_BASE, t)) deadSp.add(t);
  }
}

const out = {};
const marks = checkpoints(quick);
const byFrame = new Map();
for (const [name, f] of Object.entries(marks)) byFrame.set(f, name);

function hashNow() {
  const buf = Buffer.alloc(last.length * 4);
  for (let i = 0; i < last.length; i++) buf.writeInt32LE(last[i], i * 4);
  return crypto.createHash("sha1").update(buf).digest("hex").slice(0, 16);
}

const p = plan(quick);
const walkStart = walkStartOf(p);
const walkEnd = walkEndOf(p);
let frame = 0;

function tick() {
  for (const [name, down] of inputsFor(p, frame)) {
    if (down) nes.buttonDown(1, BUTTON[name]);
    else nes.buttonUp(1, BUTTON[name]);
  }
  nes.frame();
  scanDead();
  if (byFrame.has(frame)) out[byFrame.get(frame)] = hashNow();
  frame++;
}

while (frame < walkEnd) tick();

if (!quick) {
  // --- GAME OVER screen (level finish routes here in the cut build) --------
  nes.cpu.mem[MODE] = 2;
  let reached = false;
  for (let f = 0; f < 1200; f++) {
    nes.frame();
    scanDead();
    if (nes.cpu.mem[MODE] === 3) {
      reached = true;
      break;
    }
  }
  out.gameover_reached = reached;
  if (reached) {
    for (let f = 0; f < 240; f++) {
      nes.frame();
      scanDead();
    }
    out.gameover = hashNow();
  }
}

out.rom = path.basename(romPath);
if (wantDead) out.dead = { bg: [...deadBg].sort((a, b) => a - b), sp: [...deadSp].sort((a, b) => a - b) };
console.log(JSON.stringify(out, null, 1));
