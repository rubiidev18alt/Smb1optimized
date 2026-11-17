#!/usr/bin/env node
/*
 * rom-timeline.js -- the single definition of "drive the ROM and look at it".
 *
 * Several tools need to put a ROM through the screens the cut build can show:
 * the acceptance probe hashes those screens, and the CHR repair loop compares
 * them between a blanked ROM and an unblanked reference. When those two used
 * slightly different timelines they disagreed about what was on screen, and a
 * tile that is only visible at one scroll position slipped through.
 *
 * Every tool now takes its input sequence from here, so "identical" means the
 * same thing everywhere.
 */

"use strict";

const MODE = 0x0770; // ram_oper_mode
const START = "START";
const RIGHT = "RIGHT";
const A = "A";
const B = "B";

/*
 * Phase 1 title (300 frames), phase 2 world 1-1 (2322 frames), phase 3 the
 * GAME OVER screen the level-finish path routes to. `quick` keeps the title and
 * the first 200 frames of gameplay, which is enough to catch a broken logo or a
 * missing terrain tile without paying for a full run.
 */
function plan(quick) {
  const walk = quick ? 200 : 2322;
  return {
    titleFrames: 300,
    startFrames: 4,
    settleFrames: 60,
    walkFrames: walk,
    gameover: !quick,
  };
}

/* Button events for one frame of the plan, as a list of [button, down]. */
function inputsFor(p, frame) {
  const out = [];
  if (frame === p.titleFrames) out.push([START, true]);
  if (frame === p.titleFrames + p.startFrames) out.push([START, false]);
  const walkStart = p.titleFrames + p.startFrames + p.settleFrames;
  if (frame === walkStart) {
    out.push([RIGHT, true]);
    out.push([B, true]);
  }
  if (frame >= walkStart) {
    const f = frame - walkStart;
    if (f % 45 === 0) out.push([A, true]);
    if (f % 45 === 12) out.push([A, false]);
  }
  return out;
}

const walkStartOf = (p) => p.titleFrames + p.startFrames + p.settleFrames;
const walkEndOf = (p) => walkStartOf(p) + p.walkFrames;

/* Frames at which a caller may want to fingerprint the screen. */
function checkpoints(quick) {
  const p = plan(quick);
  const marks = { title: 32 };
  if (!quick) {
    marks.title_settled = 60;
    marks.level_start = walkStartOf(p) + 200;
    marks.level_walk = walkEndOf(p) - 1;
  } else {
    marks.level_start = walkStartOf(p) + 200;
    marks.level_walk = walkEndOf(p) - 1;
  }
  return marks;
}

module.exports = { MODE, plan, inputsFor, checkpoints, walkStartOf, walkEndOf, CHECKPOINTS_ORDER: null };
