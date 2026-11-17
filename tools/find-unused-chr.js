#!/usr/bin/env node
/*
 * find-unused-chr.js -- report which CHR tiles a ROM actually displays.
 *
 * Runs the game for a while, sampling the PPU's nametables (background tiles)
 * and OAM (sprite tiles) every few frames. Any tile index never observed is a
 * candidate for blanking: the PPU addresses tiles by index, so the index must
 * stay valid, but its 16 bytes can be zeroed to make the CHR far more
 * compressible.
 *
 * Usage: node tools/find-unused-chr.js <rom> [frames]
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

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
const totalFrames = Number(process.argv[3] || 4000);

const nes = new jsnes.NES({ onFrame() {}, sampleRate: 48000, emulateSound: false });
nes.loadROM(new Uint8Array(fs.readFileSync(romPath)));
nes.ppu.palTable.loadDefaultPalette();

/*
 * Super Mario Bros. sets PPUCTRL bit 4 = 1 (background from pattern table 1 at
 * $1000) and bit 3 = 0 (sprites from pattern table 0 at $0000). The two halves
 * of CHR are therefore NOT interchangeable: tile index N means "sprite art N"
 * in table 0 and "background art N" in table 1. Blanking the wrong half erases
 * the terrain and the HUD, so keep the split explicit here.
 */
const BG_TABLE = 1;
const SP_TABLE = 0;

const usedBg = new Set();
const usedSp = new Set();

function sample() {
  // Background: nametable bytes hold the tile index within the BG table.
  const ppu = nes.ppu;
  const nt = ppu.vramMem || ppu.vram;
  if (nt) {
    for (let a = 0x2000; a < 0x2c00; a++) usedBg.add(nt[a]);
  }
  // Sprites: OAM byte 1 is the tile index within the sprite table.
  const oam = ppu.spriteMem;
  for (let s = 0; s < 64; s++) {
    const y = oam[s * 4];
    if (y < 240) usedSp.add(oam[s * 4 + 1]);
  }
}

const step = (n) => { for (let i = 0; i < n; i++) nes.frame(); };

step(300);
/*
 * Collect tiles across EVERY screen state the cut ROM can still reach, not just
 * gameplay. The build boots straight into 1-1 and shows the GAME OVER screen
 * when the level is finished, so both of those screens must be harvested or
 * blanking eats a letter ("GAME OVER" loses its V).
 */
const MODE = 0x0770;

function play(frames, sampleEvery = 4) {
  for (let f = 0; f < frames; f++) {
    if (f % 45 === 0) nes.buttonDown(1, jsnes.Controller.BUTTON_A);
    if (f % 45 === 12) nes.buttonUp(1, jsnes.Controller.BUTTON_A);
    nes.frame();
    if (f % sampleEvery === 0) sample();
  }
}

function gotoMode(target, maxFrames) {
  for (let f = 0; f < maxFrames; f++) {
    nes.frame();
    sample();
    if (nes.cpu.mem[MODE] === target) return f;
  }
  return -1;
}

// 1. Boot / title screen.
for (let f = 0; f < 300; f++) { nes.frame(); if (f % 4 === 0) sample(); }

// 2. World 1-1 gameplay: walk right, jump, stomp.
nes.buttonDown(1, jsnes.Controller.BUTTON_START); step(4);
nes.buttonUp(1, jsnes.Controller.BUTTON_START); step(60);
nes.buttonDown(1, jsnes.Controller.BUTTON_RIGHT);
nes.buttonDown(1, jsnes.Controller.BUTTON_B);
play(totalFrames);

// 3. GAME OVER screen (the level-finish path now routes here). Poke the mode
//    byte the way the game's own victory handler does.
const saved = nes.cpu.mem[MODE];
nes.cpu.mem[MODE] = 2;
const goFrames = gotoMode(3, 900);
if (goFrames >= 0) {
  // Hold on the GAME OVER screen so its whole text band gets sampled.
  for (let f = 0; f < 240; f++) { nes.frame(); sample(); }
}
nes.cpu.mem[MODE] = saved;

const bg = [...usedBg].sort((a, b) => a - b);
const sp = [...usedSp].sort((a, b) => a - b);
console.log(JSON.stringify({
  frames: totalFrames,
  bg_tiles_used: bg.length,
  sp_tiles_used: sp.length,
  bg: bg,
  sp: sp,
}, null, 1));
