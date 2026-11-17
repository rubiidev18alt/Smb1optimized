// Load the REAL page (index.html + app.js) in jsdom against the live server.
/*
 * page-test.js -- end-to-end test of the actual browser harness.
 *
 * Loads web/index.html + web/app.js in jsdom, fetches the ROM from the running
 * server exactly as the browser does, and asserts that frames get painted.
 * Requires the server to be up:  node server.js &
 *
 * Usage: node tools/page-test.js [baseUrl]
 */

const { JSDOM, VirtualConsole } = require("jsdom");
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const BASE = process.argv[2] || process.env.BASE_URL || "http://127.0.0.1:8000";
const WEB = path.join(ROOT, "web");

const vc = new VirtualConsole();
const errors = [];
vc.on("jsdomError", e => errors.push("jsdomError: " + e.message));
vc.on("error", (...a) => errors.push("console.error: " + a.join(" ")));

async function main() {
  const html = fs.readFileSync(path.join(WEB, "index.html"), "utf8");
  const dom = new JSDOM(html, {
    url: BASE + "/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
    resources: undefined,
    virtualConsole: vc,
  });
  const { window } = dom;

  // canvas 2D is not implemented in jsdom; record putImageData calls instead.
  let painted = 0, lastPixels = null;
  window.HTMLCanvasElement.prototype.getContext = function () {
    return {
      imageSmoothingEnabled: false,
      createImageData: (w, h) => ({ width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }),
      putImageData: (img) => { painted++; lastPixels = img.data; },
    };
  };
  window.requestAnimationFrame = (cb) => setTimeout(() => cb(performance.now()), 0);
  window.cancelAnimationFrame = (id) => clearTimeout(id);

  // Load jsnes the same way the page does: a plain script tag -> global.
  const jsnesSrc = fs.readFileSync(path.join(ROOT, "node_modules/jsnes/dist/jsnes.min.js"), "utf8");
  window.eval(jsnesSrc);
  window.console.log("jsnes global keys:", Object.keys(window.jsnes || {}).join(","));

  // app.js uses fetch() against "/rom"
  window.fetch = (url) => fetch(new URL(url, BASE + "/").href);
  window.performance = window.performance || { now: () => Date.now() };

  // If globals failed to attach to window, inject them explicitly.
  if (!window.jsnes) {
    const j = window.eval("(typeof jsnes !== 'undefined' ? jsnes : undefined)");
    window.console.log("recovered:", !!j);
    window.jsnes = j;
  }

  const appSrc = fs.readFileSync(path.join(WEB, "app.js"), "utf8");
  window.eval(appSrc);

  // Give the async boot() a moment to fetch + load the ROM.
  await new Promise(r => setTimeout(r, 1500));

  const info = window.document.getElementById("rom-info").textContent;
  const meta = window.document.getElementById("meta").textContent;
  const overlayHidden = window.document.getElementById("overlay").hidden;

  // Press the overlay -> start() -> frames should paint.
  window.eval("document.getElementById('overlay').dispatchEvent(new Event('click'))");
  await new Promise(r => setTimeout(r, 800));

  const fps = window.document.getElementById("fps").textContent;

  console.log("rom-info :", info);
  console.log("meta     :", meta);
  console.log("painted  :", painted, "frames");
  console.log("fps text :", fps);

  let ok = true;
  function check(cond, label) {
    console.log((cond ? "  PASS " : "  FAIL ") + label);
    if (!cond) ok = false;
  }
  console.log("\nchecks:");
  check(/CRC32 3337EC46/.test(info), "page read the ROM and computed CRC32 3337EC46");
  check(painted > 5, `app.js painted frames (${painted})`);
  check(/fps: [1-9]/.test(fps), "rAF loop is running and counting fps");
  check(errors.filter(e => !/Not implemented/.test(e)).length === 0,
        "no page errors: " + (errors.filter(e=>!/Not implemented/.test(e)).join("; ") || "none"));

  // verify the painted pixels look like SMB1 (colour variety)
  if (lastPixels) {
    const seen = new Set();
    for (let i = 0; i < lastPixels.length; i += 4)
      seen.add((lastPixels[i] << 16) | (lastPixels[i+1] << 8) | lastPixels[i+2]);
    console.log("  distinct colours in last painted frame:", seen.size);
    check(seen.size > 5, "painted image has multiple colours (not a black screen)");
  }

  console.log(ok ? "\nPAGE TEST PASS" : "\nPAGE TEST FAIL");
  process.exit(ok ? 0 : 1);
}
main().catch(e => { console.error(e); process.exit(1); });
