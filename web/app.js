/*
 * app.js -- SMB1 browser test harness.
 *
 * Fetches the ROM from /rom, runs it in jsnes, and paints frames. The loop is
 * locked to the NES's real ~60.0988 Hz, audio is scheduled through an
 * AudioBufferSourceNode ring via a ScriptProcessor, and the frame buffer is
 * converted with the native byte order so the ARGB ints come out right.
 */
"use strict";

(function () {
  const canvas = document.getElementById("screen");
  const ctx = canvas.getContext("2d", { alpha: false });
  ctx.imageSmoothingEnabled = false;

  const overlay = document.getElementById("overlay");
  const overlayTitle = document.getElementById("overlay-title");
  const overlayText = document.getElementById("overlay-text");
  const metaEl = document.getElementById("meta");
  const romInfoEl = document.getElementById("rom-info");
  const fpsEl = document.getElementById("fps");

  const WIDTH = 256;
  const HEIGHT = 240;
  const FPS = 60.0988;
  const FRAME_MS = 1000 / FPS;

  const imageData = ctx.createImageData(WIDTH, HEIGHT);
  const pixels32 = new Uint32Array(imageData.data.buffer);

  // jsnes hands back 0x00RRGGBB; ImageData wants RGBA in native byte order.
  const LITTLE_ENDIAN = new Uint8Array(new Uint32Array([0x01020304]).buffer)[0] === 0x04;
  const ALPHA = 0xff000000;
  if (LITTLE_ENDIAN) {
    for (let i = 0; i < pixels32.length; i++) pixels32[i] = ALPHA;
  } else {
    for (let i = 0; i < pixels32.length; i++) pixels32[i] = 0x000000ff;
  }

  // ---------------------------------------------------------------- state
  let nes = null;
  let running = false;
  let animId = null;
  let lastFrameTime = 0;
  let accumulator = 0;
  let frameCount = 0;
  let fpsWindowStart = performance.now();
  let pendingAudio = [];

  // button name -> jsnes Controller.BUTTON_* code
  const BUTTON = {};
  if (window.jsnes && window.jsnes.Controller) {
    BUTTON.a = window.jsnes.Controller.BUTTON_A;
    BUTTON.b = window.jsnes.Controller.BUTTON_B;
    BUTTON.select = window.jsnes.Controller.BUTTON_SELECT;
    BUTTON.start = window.jsnes.Controller.BUTTON_START;
    BUTTON.up = window.jsnes.Controller.BUTTON_UP;
    BUTTON.down = window.jsnes.Controller.BUTTON_DOWN;
    BUTTON.left = window.jsnes.Controller.BUTTON_LEFT;
    BUTTON.right = window.jsnes.Controller.BUTTON_RIGHT;
  } else {
    // Documented jsnes codes, used only if the globals are unavailable.
    Object.assign(BUTTON, { a: 0, b: 1, select: 2, start: 3, up: 4, down: 5, left: 6, right: 7 });
  }

  // ---------------------------------------------------------------- audio
  let audioCtx = null;
  let audioNode = null;
  let soundOn = true;

  function initAudio() {
    if (audioCtx) return;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;

    audioCtx = new AC({ sampleRate: 48000 });
    const bufferSize = 4096;
    audioNode = audioCtx.createScriptProcessor(bufferSize, 1, 1);

    // jsnes pushes individual samples via onAudioSample; queue them and drain
    // into the output buffer, padding with silence on underrun.
    audioNode.onaudioprocess = (e) => {
      const out = e.outputBuffer.getChannelData(0);
      const n = Math.min(out.length, pendingAudio.length);
      for (let i = 0; i < n; i++) out[i] = pendingAudio[i];
      if (n < out.length) out.fill(0, n);
      pendingAudio = pendingAudio.length > n
        ? pendingAudio.slice(n)
        : [];
    };

    audioNode.connect(audioCtx.destination);
    audioCtx.resume().catch(() => {});
  }

  function onAudioSample(left) {
    if (!soundOn) return;
    // keep a bounded backlog so a backgrounded tab can't grow it forever
    if (pendingAudio.length < 16384) pendingAudio.push(left);
  }

  // ---------------------------------------------------------------- rom load
  async function fetchRom() {
    const res = await fetch("/rom", { cache: "no-store" });
    if (!res.ok) throw new Error(`ROM fetch failed: HTTP ${res.status}`);
    const buf = new Uint8Array(await res.arrayBuffer());
    if (buf.length < 16 || buf[0] !== 0x4e || buf[1] !== 0x45 || buf[2] !== 0x53) {
      throw new Error("response is not an iNES ROM");
    }
    return buf;
  }

  function describeRom(bytes) {
    const prg = bytes[4] * 16;
    const chr = bytes[5] * 8;
    const crc = crc32(bytes);
    return `${prg}K PRG + ${chr}K CHR · ${bytes.length} bytes · CRC32 ${crc}`;
  }

  function crc32(bytes) {
    let table = crc32.table;
    if (!table) {
      table = crc32.table = new Uint32Array(256);
      for (let i = 0; i < 256; i++) {
        let c = i;
        for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
        table[i] = c >>> 0;
      }
    }
    let crc = 0xffffffff;
    for (let i = 0; i < bytes.length; i++) crc = table[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
    return ((crc ^ 0xffffffff) >>> 0).toString(16).toUpperCase().padStart(8, "0");
  }

  /*
   * jsnes's shipped NTSC table is misaligned: it maps colour $22 to #FF7981
   * (salmon) so SMB1's blue sky renders pink and the orange-brown ground bricks
   * render dark blue. loadDefaultPalette() uses the measured 2C02 values
   * ($22 -> #5F97FF sky, bricks -> #CB4F0F). Apply it on boot and after reset,
   * since reset() builds a fresh PPU.
   */
  function applyAccuratePalette() {
    try {
      if (nes && nes.ppu && nes.ppu.palTable && nes.ppu.palTable.loadDefaultPalette) {
        nes.ppu.palTable.loadDefaultPalette();
      }
    } catch (e) {
      console.warn("palette override failed; using jsnes default", e);
    }
  }

  // ---------------------------------------------------------------- frames
  function onFrame(buffer) {
    // buffer is a Uint32Array(256*240) of 0x00RRGGBB
    for (let i = 0; i < pixels32.length; i++) {
      pixels32[i] = (pixels32[i] & 0xff000000) | buffer[i];
    }
    ctx.putImageData(imageData, 0, 0);
    frameCount++;
  }

  function tick(now) {
    if (!running) return;
    if (!lastFrameTime) lastFrameTime = now;
    accumulator += now - lastFrameTime;
    lastFrameTime = now;

    // Never try to catch up more than a few frames (e.g. after a tab switch).
    if (accumulator > FRAME_MS * 4) accumulator = FRAME_MS * 4;

    let steps = 0;
    while (accumulator >= FRAME_MS && steps < 4) {
      nes.frame();
      accumulator -= FRAME_MS;
      steps++;
    }

    if (now - fpsWindowStart >= 1000) {
      fpsEl.textContent = `fps: ${frameCount}`;
      frameCount = 0;
      fpsWindowStart = now;
    }

    animId = requestAnimationFrame(tick);
  }

  // ---------------------------------------------------------------- input
  const held = new Set();

  const KEYMAP = {
    ArrowLeft: "left",
    ArrowRight: "right",
    ArrowUp: "up",
    ArrowDown: "down",
    KeyA: "left",
    KeyD: "right",
    KeyW: "up",
    KeyS: "down",
    KeyZ: "a",
    KeyK: "a",
    KeyX: "b",
    KeyJ: "b",
    Space: "a",
    Enter: "start",
    ShiftLeft: "select",
    ShiftRight: "select",
  };

  function setButton(name, down) {
    if (!nes || BUTTON[name] === undefined) return;
    const code = BUTTON[name];
    if (down) {
      if (held.has(name)) return;
      held.add(name);
      nes.buttonDown(1, code);
    } else {
      if (!held.delete(name)) return;
      nes.buttonUp(1, code);
    }
  }

  function releaseAll() {
    for (const name of Array.from(held)) setButton(name, false);
  }

  window.addEventListener("keydown", (e) => {
    const name = KEYMAP[e.code];
    if (!name) return;
    e.preventDefault();
    if (!running) start();
    setButton(name, true);
  });

  window.addEventListener("keyup", (e) => {
    const name = KEYMAP[e.code];
    if (!name) return;
    e.preventDefault();
    setButton(name, false);
  });

  window.addEventListener("blur", releaseAll);

  // touch / pointer buttons
  const PADMAP = { a: "a", b: "b", start: "start", select: "select",
                   up: "up", down: "down", left: "left", right: "right" };

  document.querySelectorAll(".pad [data-btn]").forEach((btn) => {
    const name = PADMAP[btn.dataset.btn];
    if (!name) return;
    const press = (e) => {
      e.preventDefault();
      if (!running) start();
      setButton(name, true);
    };
    const release = (e) => {
      e.preventDefault();
      setButton(name, false);
    };
    btn.addEventListener("pointerdown", press);
    btn.addEventListener("pointerup", release);
    btn.addEventListener("pointercancel", release);
    btn.addEventListener("pointerleave", release);
    btn.addEventListener("contextmenu", (e) => e.preventDefault());
  });

  // ---------------------------------------------------------------- controls
  function showOverlay(title, text) {
    overlayTitle.textContent = title;
    overlayText.textContent = text;
    overlay.hidden = false;
  }

  function hideOverlay() {
    overlay.hidden = true;
  }

  function start() {
    if (!nes) return;
    initAudio();
    if (audioCtx && audioCtx.state === "suspended") audioCtx.resume().catch(() => {});
    hideOverlay();
    running = true;
    lastFrameTime = 0;
    accumulator = 0;
    if (animId) cancelAnimationFrame(animId);
    animId = requestAnimationFrame(tick);
  }

  function pause() {
    running = false;
    releaseAll();
    if (animId) cancelAnimationFrame(animId);
    animId = null;
    showOverlay("Paused", "Click or press any key to resume.");
  }

  document.getElementById("btn-boot").addEventListener("click", () => {
    if (running) pause(); else start();
  });

  document.getElementById("btn-reset").addEventListener("click", () => {
    if (!nes) return;
    nes.reset();
    applyAccuratePalette();
    pendingAudio = [];
    start();
  });

  const soundBtn = document.getElementById("btn-sound");
  soundBtn.addEventListener("click", () => {
    soundOn = !soundOn;
    soundBtn.setAttribute("aria-pressed", String(soundOn));
    soundBtn.textContent = soundOn ? "🔊 Sound" : "🔇 Muted";
    if (!soundOn) pendingAudio = [];
    else initAudio();
  });

  document.getElementById("btn-fullscreen").addEventListener("click", () => {
    const stage = document.querySelector(".stage");
    if (!document.fullscreenElement) {
      (stage.requestFullscreen || stage.webkitRequestFullscreen || (() => {})).call(stage);
    } else {
      document.exitFullscreen().catch(() => {});
    }
  });

  overlay.addEventListener("click", start);

  // ---------------------------------------------------------------- boot
  (async function boot() {
    try {
      const romBytes = await fetchRom();
      romInfoEl.textContent = `ROM: ${describeRom(romBytes)}`;
      romInfoEl.className = "ok";

      const JSNES = window.jsnes;
      if (!JSNES || !JSNES.NES) throw new Error("jsnes failed to load");

      nes = new JSNES.NES({
        onFrame,
        onAudioSample,
        onStatusUpdate: (s) => { if (s) metaEl.textContent = s; },
        sampleRate: 48000,
        emulateSound: true,
      });

      nes.loadROM(romBytes);
      applyAccuratePalette();
      metaEl.textContent = "ROM loaded — ready";
      // Render the first frame so the screen isn't blank behind the overlay.
      nes.frame();
      showOverlay("Press Start", "Click the screen or press any key to play.");
    } catch (err) {
      console.error(err);
      romInfoEl.textContent = `error: ${err.message}`;
      romInfoEl.className = "warn";
      showOverlay("Could not start", err.message);
    }
  })();
})();
