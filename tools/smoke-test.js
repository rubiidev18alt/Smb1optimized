#!/usr/bin/env node
/*
 * smoke-test.js -- headless proof that the ROM actually boots and renders.
 *
 * Runs the ROM in jsnes for N emulated frames and asserts that the PPU produced
 * a real picture (many colours, sky-blue dominant, non-trivial content). This
 * catches the failure mode where a "working" harness silently shows a black
 * screen forever.
 *
 * Usage: node tools/smoke-test.js [frames]
 */

"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
// An absolute path is used as-is; a bare name is resolved against the repo root.
const ROM = path.resolve(ROOT, process.env.ROM || "Super Mario Bros. (JU) (PRG0) [!].nes");
const FRAMES = Number(process.argv[2] || 300);

/*
 * jsnes 3.x ships a UMD bundle but declares "type": "module", so a plain
 * require() under Node gets loaded as ESM and the UMD wrapper parks its exports
 * on globalThis instead of module.exports. Evaluate the bundle in a sandbox with
 * a CommonJS-shaped `module` to force the UMD CommonJS branch -- the browser
 * path is unaffected because it gets a plain <script> tag.
 */
function loadJsnes() {
  const vm = require("vm");
  const file = path.join(ROOT, "node_modules", "jsnes", "dist", "jsnes.js");
  if (!fs.existsSync(file)) {
    console.error("jsnes not installed. Run: npm install");
    process.exit(1);
  }
  const sandbox = { exports: {}, module: { exports: {} } };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(file, "utf8"), sandbox, { filename: file });
  return sandbox.module.exports;
}

const jsnes = loadJsnes();

const W = 256;
const H = 240;
// With the measured 2C02 palette applied (see below), SMB1 renders as intended:
const TITLE_BG = 0x5f97ff; // title screen backdrop, colour $22
const SKY = 0x5f97ff;      // overworld sky, also colour $22
const BRICK = 0xcb4f0f;    // World 1-1 ground/brick blocks

function main() {
  if (!fs.existsSync(ROM)) {
    console.error(`ROM not found: ${ROM}`);
    return 1;
  }
  const rom = new Uint8Array(fs.readFileSync(ROM));
  if (rom[0] !== 0x4e || rom[1] !== 0x45 || rom[2] !== 0x53) {
    console.error("not an iNES ROM");
    return 1;
  }

  let frames = 0;
  let audioSamples = 0;
  let last = null;

  const nes = new jsnes.NES({
    onFrame: (buf) => { frames++; last = buf; },
    onAudioSample: () => { audioSamples++; },
    sampleRate: 48000,
    emulateSound: true,
  });

  nes.loadROM(rom);

  /*
   * jsnes ships a misaligned NTSC table ($22 -> salmon), which makes the sky
   * pink and the bricks blue. loadDefaultPalette() has the measured 2C02
   * values; apply it the same way the browser harness does.
   */
  if (nes.ppu && nes.ppu.palTable && nes.ppu.palTable.loadDefaultPalette) {
    nes.ppu.palTable.loadDefaultPalette();
  }

  function stats(buf) {
    const counts = new Map();
    for (let i = 0; i < buf.length; i++) {
      const c = buf[i] & 0xffffff;
      counts.set(c, (counts.get(c) || 0) + 1);
    }
    return counts;
  }

  function press(button, frames) {
    nes.buttonDown(1, button);
    for (let i = 0; i < frames; i++) nes.frame();
    nes.buttonUp(1, button);
  }

  // --- phase 1: boot to the title screen ---
  for (let i = 0; i < FRAMES; i++) nes.frame();

  if (!last) {
    console.error("no frame was produced — PPU never ran");
    return 1;
  }

  const problems = [];

  // --- title screen checks ---
  let counts = stats(last);
  let nonBlack = 0;
  for (const [c, n] of counts) if (c !== 0) nonBlack += n;
  const titleBg = counts.get(TITLE_BG) || 0;
  const titleColours = counts.size;
  const titleFrames = frames;

  console.log(`ROM          : ${path.basename(ROM)} (${rom.length} bytes)`);
  console.log(`audio        : ${audioSamples} samples generated`);
  console.log("");
  console.log(`title screen : ${titleFrames} frames in`);
  console.log(`  colours    : ${titleColours} distinct`);
  console.log(`  backdrop   : ${titleBg} px (#5f97ff)`);
  console.log(`  non-black  : ${nonBlack}/${W * H} (${(100 * nonBlack / (W * H)).toFixed(1)}%)`);

  if (titleColours < 6) problems.push(`title screen has only ${titleColours} colours — looks blank`);
  if (titleBg < 8000) problems.push(`title backdrop is ${titleBg}px, expected >8000`);
  if (nonBlack < W * H * 0.25) problems.push("title frame is mostly black");
  if (audioSamples < 1000) problems.push(`APU produced only ${audioSamples} samples`);

  // --- phase 2: press START and reach World 1-1 ---
  const START = jsnes.Controller.BUTTON_START;
  press(START, 3);
  for (let i = 0; i < 260; i++) nes.frame();

  counts = stats(last);
  const skyPixels = counts.get(SKY) || 0;
  const brickPixels = counts.get(BRICK) || 0;
  const gameColours = counts.size;
  console.log("");
  console.log(`world 1-1    : after START + 260 frames`);
  console.log(`  colours    : ${gameColours} distinct`);
  console.log(`  sky        : ${skyPixels} px (#5f97ff)`);
  console.log(`  bricks     : ${brickPixels} px (#cb4f0f)`);

  if (skyPixels < 5000) {
    problems.push(`gameplay sky is ${skyPixels}px, expected >5000 — START did not reach 1-1`);
  }
  if (brickPixels < 1000) {
    problems.push(`ground bricks are ${brickPixels}px, expected >1000 — wrong palette?`);
  }
  if (gameColours < 6) problems.push(`gameplay screen has only ${gameColours} colours`);

  /*
   * Blanked CHR guard. A tile that is on screen while its 16 pattern bytes are
   * all zero means the art is missing and the picture is wrong -- exactly the
   * bug that erased the title logo when blanking the wrong pattern table. Skip
   * tile 0, which is legitimately empty in SMB1 (it is the blank cell the game
   * fills the screen with).
   */
  {
    const chrStart = 16 + (rom[4] === 2 ? 32768 : rom[4] * 16384);
    const bgBase = chrStart + 256 * 16;
    const spBase = chrStart;
    const isBlank = (base, idx) => {
      const off = base + idx * 16;
      for (let i = 0; i < 16; i++) if (rom[off + i] !== 0) return false;
      return true;
    };
    /*
     * Compare against the reference ROM rather than assuming every displayed
     * tile must hold data: SMB1 legitimately draws empty tiles (the blank cell
     * it clears the screen with, and an empty 8x8 among Mario's sprite set). A
     * tile is only missing if the original ROM had art for it and this one does
     * not.
     */
    const refPath = path.join(ROOT, "Super Mario Bros. (JU) (PRG0) [!].nes");
    const ref = fs.existsSync(refPath) ? fs.readFileSync(refPath) : null;
    const refChr = ref
      ? 16 + (ref[4] === 2 ? 32768 : ref[4] * 16384)
      : null;
    const wasDrawn = (base, refBase, idx) => {
      if (!ref) return true;
      const off = refBase + idx * 16;
      for (let i = 0; i < 16; i++) if (ref[off + i] !== 0) return true;
      return false;
    };
    const missing = new Set();
    for (let a = 0x2000; a < 0x2c00; a++) {
      const t = nes.ppu.vramMem[a];
      if (t === 0 || !isBlank(bgBase, t)) continue;
      if (wasDrawn(bgBase, refChr + 256 * 16, t)) missing.add("bg#" + t);
    }
    const oam = nes.ppu.spriteMem;
    for (let s = 0; s < 64; s++) {
      if (oam[s * 4] >= 240) continue;
      const t = oam[s * 4 + 1];
      if (t === 0 || !isBlank(spBase, t)) continue;
      if (wasDrawn(spBase, refChr, t)) missing.add("sp#" + t);
    }
    if (missing.size) {
      problems.push(
        `${missing.size} displayed tile(s) have no CHR data: ${[...missing].join(", ")}`
      );
    }
  }

  // Optional: dump the frame for eyeballing.
  const out = process.env.PPM_OUT;
  if (out) {
    const rgb = Buffer.alloc(W * H * 3);
    for (let i = 0; i < W * H; i++) {
      const c = last[i];
      rgb[i * 3] = (c >> 16) & 0xff;
      rgb[i * 3 + 1] = (c >> 8) & 0xff;
      rgb[i * 3 + 2] = c & 0xff;
    }
    fs.writeFileSync(out, Buffer.concat([Buffer.from(`P6\n${W} ${H}\n255\n`, "ascii"), rgb]));
    console.log(`frame dump  : ${out}`);
  }

  if (problems.length) {
    console.error("\nFAIL");
    for (const p of problems) console.error(`  - ${p}`);
    return 1;
  }
  console.log("\nPASS — boots, renders the title screen, accepts START, and draws World 1-1");
  return 0;
}

process.exit(main());
