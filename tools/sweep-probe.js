#!/usr/bin/env node
/*
 * sweep-probe.js -- walk the *whole* level, not just the part a bot reaches.
 *
 * A walking bot never gets past the first pit, so any check that only drives it
 * for a few thousand frames silently ignores most of world 1-1: the fire
 * flower, the hard blocks, the flagpole and everything else further right.
 * That is exactly how blanked tiles for those objects slipped through.
 *
 * This harness teleports the camera and the player to every page of the level,
 * gives the area parser time to build each screen, and fingerprints all of it.
 * Two ROMs that produce the same fingerprints show the same game wherever the
 * player can walk to.
 *
 * Usage: node tools/sweep-probe.js <rom> [--dead] [--pages N]
 * Output: JSON {pages: {<n>: hash}, dead: {bg,sp}}
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const crypto = require("crypto");

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
const wantDead = process.argv.includes("--dead");
const pageArg = process.argv.indexOf("--pages");
const maxPages = pageArg > 0 ? Number(process.argv[pageArg + 1]) : 16;

if (!romPath) {
  console.error("usage: node tools/sweep-probe.js <rom> [--dead] [--pages N]");
  process.exit(2);
}

const rom = fs.readFileSync(romPath);
const CHR_START = 16 + (rom[4] === 2 ? 32768 : rom[4] * 16384);
const BG_BASE = CHR_START + 256 * 16;
const SP_BASE = CHR_START;
const slotIsBlank = (base, i) => {
  const off = base + i * 16;
  for (let k = 0; k < 16; k++) if (rom[off + k] !== 0) return false;
  return true;
};

// --- SMB RAM addresses (see build/src/smb1_src/src/memory/ram.inc) ----------
const PLAYER_PAGE = 0x0086; // ram_player_x_position, 256-px page of world X
const PLAYER_X_SCROLL = 0x06ff; // sub-page
const SCREEN_LEFT_PAGE = 0x071a; // camera page
const SCREEN_LEFT_X = 0x071c; // camera sub-page
const SCREEN_RIGHT_PAGE = 0x071b;
const SCREEN_RIGHT_X = 0x071d;
const PLAYER_Y_POS = 0x00ce; // ram_player_y_position
const PLAYER_Y_HIGH = 0x00b5; // ram_player_y_high_pos
const MODE = 0x0770;
const PLAYER_STATUS = 0x0756; // ram_player_status: 0 small, 1 big, 2 fire
/*
 * jsnes exports its button constants as BUTTON_START, BUTTON_RIGHT, ... on the
 * Controller object. A `Controller.BUTTON_START` style lookup silently yields
 * undefined, nes.buttonDown(undefined) does nothing, and the harness happily
 * "plays" a game it never pressed a button on. Resolve them the same way
 * tools/chr-probe.js does.
 */
const BUTTON = {
  START: jsnes.Controller.BUTTON_START,
  RIGHT: jsnes.Controller.BUTTON_RIGHT,
  A: jsnes.Controller.BUTTON_A,
  B: jsnes.Controller.BUTTON_B,
};

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

const deadBg = new Set();
const deadSp = new Set();
function scan() {
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

const run = (n) => {
  for (let i = 0; i < n; i++) {
    nes.frame();
    scan();
    scanUsed();
  }
};

function hashFrame() {
  const buf = Buffer.alloc(last.length * 4);
  for (let i = 0; i < last.length; i++) buf.writeInt32LE(last[i], i * 4);
  return crypto.createHash("sha1").update(buf).digest("hex").slice(0, 16);
}

/*
 * Teleport the player and the camera to a page. The area parser streams scenery
 * in as the camera moves, so hold the position for a while and let it build the
 * screen before fingerprinting it. The player is dropped back to the top of the
 * page each time so it lands on ground rather than inside a wall.
 */
function gotoPage(page) {
  const m = nes.cpu.mem;
  m[PLAYER_PAGE] = page & 0xff;
  m[PLAYER_X_SCROLL] = 0x40;
  /*
   * The vertical position has to be set too. Moving the camera alone leaves the
   * player wherever he was, which is inside empty sky at the new page: he falls,
   * dies, respawns at the start, and the "swept" page is never actually seen.
   * $1b0 is ground level in world 1-1.
   */
  m[PLAYER_Y_POS] = 0xb0;
  m[PLAYER_Y_HIGH] = 0x01;
  m[SCREEN_LEFT_PAGE] = page & 0xff;
  m[SCREEN_LEFT_X] = 0x00;
  m[SCREEN_RIGHT_PAGE] = (page + 1) & 0xff;
  m[SCREEN_RIGHT_X] = 0x00;
}

const usedBg = new Set();
const usedSp = new Set();
function scanUsed() {
  const nt = nes.ppu.vramMem;
  if (nt) for (let a = 0x2000; a < 0x2c00; a++) usedBg.add(nt[a]);
  const oam = nes.ppu.spriteMem;
  for (let s = 0; s < 64; s++) if (oam[s * 4] < 240) usedSp.add(oam[s * 4 + 1]);
}

const out = { pages: {} };

// Boot, title, then start the game so world 1-1 exists.
run(300);
nes.buttonDown(1, BUTTON.START);
run(4);
nes.buttonUp(1, BUTTON.START);
run(240);
out.mode_at_start = nes.cpu.mem[MODE];

/*
 * Multi-shot mode: give the player a fire flower and fireballs, so the fire
 * sprites and the grown-Mario art get drawn too. Both are ordinary gameplay
 * states, and the art for them is exactly what a harvest misses.
 */
/*
 * World 1-1 has no flower, so fire Mario never happens on its own. Grant it the
 * way the game does: ram_player_status holds the power level (0 small, 1 big,
 * 2 fire), and the sprite set follows from it.
 */
nes.cpu.mem[PLAYER_STATUS] = 2;
run(30);
out.player_status = nes.cpu.mem[PLAYER_STATUS];

for (let page = 0; page < maxPages; page++) {
  gotoPage(page);
  run(220); // let the parser stream this screen in
  // Walk a little so actors spawn and the camera settles.
  nes.buttonDown(1, BUTTON.RIGHT);
  run(90);
  nes.buttonUp(1, BUTTON.RIGHT);
  // Throw fireballs on every other page.
  if (page % 2 === 0) {
    nes.buttonDown(1, BUTTON.B);
    run(6);
    nes.buttonUp(1, BUTTON.B);
    run(40);
  }
  out.pages[page] = hashFrame();
}

// The flagpole is at the very end of the level; sit there for a while so the
// end-of-level actors render.
gotoPage(Math.max(0, maxPages - 1));
run(300);
out.final_page = hashFrame();

/*
 * GAME OVER screen. Finishing 1-1 in this build routes here, and the screen lays
 * out text tiles that appear on no other screen -- "GAME OVER" is drawn with a
 * different tile for its V than the title screen uses for the same letter. A
 * sweep that stops at the level would leave those tiles looking unused.
 */
nes.cpu.mem[MODE] = 0x02;
let reachedGameOver = false;
for (let f = 0; f < 1200; f++) {
  nes.frame();
  scan();
  scanUsed();
  if (nes.cpu.mem[MODE] === 0x03) {
    reachedGameOver = true;
    break;
  }
}
if (reachedGameOver) {
  run(240);
  out.gameover = hashFrame();
}
out.gameover_reached = reachedGameOver;

out.dead = { bg: [...deadBg].sort((a, b) => a - b), sp: [...deadSp].sort((a, b) => a - b) };
out.used = { bg: [...usedBg].sort((a, b) => a - b), sp: [...usedSp].sort((a, b) => a - b) };
out.rom = path.basename(romPath);
console.log(JSON.stringify(out, null, 1));
