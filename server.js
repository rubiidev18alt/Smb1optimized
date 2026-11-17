#!/usr/bin/env node
/*
 * server.js -- tiny static server for the SMB1 browser test harness.
 *
 * Serves web/ plus a /rom endpoint that streams the ROM straight from the repo,
 * so the page can never drift out of sync with the file under test.
 *
 * Binds 0.0.0.0 so the sandbox preview proxy can reach it.
 */

"use strict";

const http = require("http");
const fs = require("fs");
const path = require("path");
const { URL } = require("url");

const ROOT = __dirname;
const WEB = path.join(ROOT, "web");
const ROM_DIR = ROOT; // ROM lives at the repo root
const ROM_NAME =
  process.env.ROM ||
  "Super Mario Bros. (JU) (PRG0) [!].nes";
const ROM_PATH = path.join(ROM_DIR, ROM_NAME);

const PORT = Number(process.env.PORT) || 8000;
const HOST = process.env.HOST || "0.0.0.0";

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".nes": "application/octet-stream",
  ".smbz": "application/octet-stream",
  ".map": "application/json",
};

function send(res, status, body, headers = {}) {
  res.writeHead(status, {
    "Cache-Control": "no-store",
    ...headers,
  });
  res.end(body);
}

function serveFile(res, filePath) {
  fs.readFile(filePath, (err, data) => {
    if (err) {
      send(res, 404, "not found");
      return;
    }
    const type = MIME[path.extname(filePath).toLowerCase()] || "application/octet-stream";
    res.writeHead(200, {
      "Content-Type": type,
      "Content-Length": data.length,
      "Cache-Control": "no-store",
    });
    res.end(data);
  });
}

const server = http.createServer((req, res) => {
  let pathname;
  try {
    pathname = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
  } catch {
    send(res, 400, "bad request");
    return;
  }

  if (pathname === "/" || pathname === "/index.html") {
    serveFile(res, path.join(WEB, "index.html"));
    return;
  }

  // The ROM under test, served from the repo root.
  if (pathname === "/rom") {
    serveFile(res, ROM_PATH);
    return;
  }

  // jsnes straight out of node_modules (no bundler needed: dist/ is UMD).
  if (pathname === "/vendor/jsnes.js" || pathname === "/vendor/jsnes.min.js") {
    const which = pathname.endsWith(".min.js") ? "jsnes.min.js" : "jsnes.js";
    serveFile(res, path.join(ROOT, "node_modules", "jsnes", "dist", which));
    return;
  }

  if (pathname === "/healthz") {
    send(res, 200, "ok");
    return;
  }

  // Anything else: static from web/, with traversal protection.
  const rel = path.normalize(pathname).replace(/^(\.\.[/\\])+/, "");
  const target = path.join(WEB, rel);
  if (!target.startsWith(WEB)) {
    send(res, 403, "forbidden");
    return;
  }
  serveFile(res, target);
});

server.listen(PORT, HOST, () => {
  const exists = fs.existsSync(ROM_PATH);
  console.log(`SMB1 emulator harness listening on http://${HOST}:${PORT}`);
  console.log(`  ROM: ${ROM_PATH} (${exists ? "found" : "MISSING"})`);
  if (!exists) {
    console.error(`  ERROR: ROM not found. Set ROM=<filename> to override.`);
    process.exitCode = 1;
  }
});
