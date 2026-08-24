#!/usr/bin/env node
// Smoke-tests the STATIC WASM replay viewer bundle — the artifact the
// observatory actually serves — by loading a replay and rendering frames
// inside the wasm32 runtime, exactly as the page's Worker does.
//
// Why (from coworld-ctf, whose structure this viewer copies): the shipped
// module is --cpu:wasm32 — `int` is 32 bits and the address space ends at
// 2 GB. Overflow traps and allocation failures there are invisible to a
// native 64-bit run, so CI loads the exact emitted module.
//
// Usage: node tools/wasm_replay_smoke.cjs <dist-dir> <replay-file|canary> [frames]
//   Renders [frames] frames (default 60), stepping the replay forward with
//   the same `b:<beat>` command the page sends and checking that every
//   packet parses as a sprite-protocol stream. `canary` generates an
//   address-space canary replay on the fly (12 holes x ~200 beats with long
//   strings) to prove the wasm32 runtime neither aborts nor leaks across a
//   long scrub; the heap after must stay under 1 GB.

'use strict';
const fs = require('fs');
const path = require('path');

const distDir = path.resolve(process.argv[2] || 'viewer/dist');
const replayPath = process.argv[3];
const frameBudget = parseInt(process.argv[4] || '60', 10);
if (!replayPath) {
  console.error('usage: wasm_replay_smoke.cjs <dist-dir> <replay-file> [frames]');
  process.exit(2);
}

// A hung load (e.g. an allocation loop) must fail loudly, not stall the job.
const watchdog = setTimeout(() => {
  console.error('FAIL: smoke did not finish within 120s');
  process.exit(1);
}, 120000);

const Module = {
  locateFile: (p) => path.join(distDir, p),
  onRuntimeInitialized: run,
  onAbort: (what) => {
    // Allocation failure aborts (-s ABORTING_MALLOC=1) but leaves linear
    // memory intact: the stage buffer still says what exhausted it.
    const stage = readStageNote();
    console.error('FAIL: wasm runtime aborted: ' + what +
      (stage ? '\nruntime was: ' + stage : ''));
    process.exit(1);
  },
};

function readStageNote() {
  try {
    const length = Module._cogolf_stage_len ? Module._cogolf_stage_len() : 0;
    if (!length) return '';
    const pointer = Module._cogolf_stage_ptr();
    return Buffer.from(Module.HEAPU8.subarray(pointer, pointer + length)).toString('utf8');
  } catch (ignored) {
    return '';
  }
}

function readRuntimeError() {
  const length = Module._cogolf_error_len();
  if (length) {
    const pointer = Module._cogolf_error_ptr();
    return Buffer.from(Module.HEAPU8.subarray(pointer, pointer + length)).toString('utf8');
  }
  const stage = readStageNote();
  return stage
    ? '(no error text; runtime was: ' + stage + ')'
    : '(runtime reported no error text)';
}

function sendText(text) {
  const bytes = Buffer.from(text, 'ascii');
  const packet = Buffer.alloc(bytes.length + 3);
  packet[0] = 0x81;
  packet.writeUInt16LE(bytes.length, 1);
  bytes.copy(packet, 3);
  const pointer = Module._malloc(packet.length);
  Module.HEAPU8.set(packet, pointer);
  Module._cogolf_input(pointer, packet.length);
  Module._free(pointer);
}

// Walks a packet as sprite-protocol v1 messages; returns counts or throws.
function checkPacket() {
  const length = Module._cogolf_packet_len();
  if (length <= 0) throw new Error('empty packet');
  const pointer = Module._cogolf_packet_ptr();
  const bytes = Module.HEAPU8.subarray(pointer, pointer + length);
  let offset = 0;
  const counts = { sprites: 0, objects: 0, deletes: 0, layers: 0, viewports: 0, chrome: '' };
  while (offset < bytes.length) {
    const type = bytes[offset++];
    if (type === 0x01) {
      const id = bytes[offset] | (bytes[offset + 1] << 8);
      const compressed = bytes.readUInt32LE
        ? 0 : 0; // placeholder (Buffer API differs from Uint8Array)
      const clen = bytes[offset + 6] | (bytes[offset + 7] << 8) | (bytes[offset + 8] << 16) | (bytes[offset + 9] << 24);
      const labelLen = bytes[offset + 10 + clen] | (bytes[offset + 11 + clen] << 8);
      if (id === 4090) {
        counts.chrome = Buffer.from(bytes.subarray(offset + 12 + clen, offset + 12 + clen + labelLen)).toString('utf8');
      }
      offset += 12 + clen + labelLen;
      counts.sprites++;
    } else if (type === 0x02) { offset += 11; counts.objects++; }
    else if (type === 0x03) { offset += 2; counts.deletes++; }
    else if (type === 0x04) { }
    else if (type === 0x05) { offset += 5; counts.viewports++; }
    else if (type === 0x06) { offset += 3; counts.layers++; }
    else throw new Error('unknown message type 0x' + type.toString(16) + ' at ' + (offset - 1));
    if (offset > bytes.length) throw new Error('truncated message (type 0x' + type.toString(16) + ')');
  }
  return counts;
}

function canaryReplay() {
  // A long, wide cogolf match: every event kind, 12 holes, five tests each
  // way, and oversized strings — the shapes a real replay can reach.
  const events = [];
  const holes = [];
  const long = 'x'.repeat(300);
  let cum = [0, 0];
  for (let h = 1; h <= 12; h++) {
    events.push({ kind: 'hole_start', hole: h, spec_key: 'spec_' + h,
      title: 'Hole ' + h, prompt_head: long.slice(0, 160) });
    const seats = [];
    for (let slot = 0; slot < 2; slot++) {
      events.push({ kind: 'submission', hole: h, slot, impl_lines: 40,
        impl_chars: 3900, test_count: 5, note: long.slice(0, 200),
        fallback: slot === 1 && h % 3 === 0 ? { cause: 'timeout', baseline: 'literalist' } : null });
      const tests = [];
      for (let i = 0; i < 5; i++) {
        const outcome = ['breach', 'held', 'illegal'][(i + h) % 3];
        const shot = { kind: 'test_verdict', hole: h, slot, target_slot: 1 - slot,
          idx: i, name: 'test ' + i, args: [[1, 2, 3]], expect: [[1, 3]],
          why: long.slice(0, 120), legal: outcome !== 'illegal',
          legal_reason: outcome === 'illegal' ? 'ref_mismatch' : null,
          outcome, observed: long.slice(0, 300) };
        events.push(shot);
        tests.push({ idx: i, name: shot.name, args: shot.args, expect: shot.expect,
          why: shot.why, legal: shot.legal, legal_reason: shot.legal_reason,
          outcome, observed: shot.observed });
      }
      seats.push({ slot, impl: long, impl_lines: 40, broken: false,
        note: 'note', fallback: null, tests, par_fails: h % 5, par_total: 4 });
    }
    for (let slot = 0; slot < 2; slot++) {
      events.push({ kind: 'par_result', hole: h, slot, par_fails: h % 5, par_total: 4 });
    }
    const score = [(h % 3) - 1, 1 - (h % 3)];
    cum = [cum[0] + score[0], cum[1] + score[1]];
    events.push({ kind: 'hole_score', hole: h, score, cumulative: cum.slice() });
    holes.push({ hole: h, spec: { key: 'spec_' + h, title: 'Hole ' + h, prompt: long,
      signature: {}, examples: [], ambiguity: 'ambiguity' },
      seats, hole_score: score, cumulative: cum.slice() });
  }
  events.push({ kind: 'episode_end', reason: 'complete', scores: cum.slice(),
    killer_test: { hole: 3, slot: 0, target_slot: 1, name: 'test 1', why: 'why' } });
  return Buffer.from(JSON.stringify({
    format: 'cogame-cogolf-replay', version: 1, game_version: 'GV01',
    protocol: 'cogame.cogolf.v1', config: { holes: 12 }, seed: 7,
    deck: 'core', deck_version: 'core-1',
    names: ['canary-0', 'canary-1'], aliases: ['Ash', 'Basil'],
    holes, events, result: { scores: cum, reason: 'complete' },
  }));
}

function run() {
  const bytes = replayPath === 'canary' ? canaryReplay() : fs.readFileSync(replayPath);
  const pointer = Module._malloc(bytes.length);
  Module.HEAPU8.set(bytes, pointer);
  const loaded = Module._cogolf_load_replay(pointer, bytes.length);
  Module._free(pointer);
  if (loaded !== 1) {
    console.error('FAIL: cogolf_load_replay rejected ' + path.basename(replayPath) +
      '\n' + readRuntimeError());
    process.exit(1);
  }
  let first;
  try { first = checkPacket(); } catch (e) {
    console.error('FAIL: first packet malformed: ' + e.message);
    process.exit(1);
  }
  if (first.layers < 1 || first.viewports < 1 || first.objects < 1) {
    console.error('FAIL: first frame lacks layer/viewport/objects: ' + JSON.stringify(first));
    process.exit(1);
  }
  let chrome;
  try { chrome = JSON.parse(first.chrome); } catch (e) {
    console.error('FAIL: chrome JSON unparsable: ' + first.chrome);
    process.exit(1);
  }
  if (chrome.kind !== 'cogolf' || !chrome.board || !(chrome.board.w > 0)) {
    console.error('FAIL: chrome JSON missing board: ' + first.chrome);
    process.exit(1);
  }
  let packetBytes = 0;
  const steps = Math.max(1, chrome.beats || 1);
  for (let i = 0; i < frameBudget; i++) {
    sendText('b:' + (i % (steps + 1)));    // walks past the end once: must clamp
    if (Module._cogolf_frame() !== 1) {
      console.error('FAIL: cogolf_frame died at frame ' + i + '\n' + readRuntimeError());
      process.exit(1);
    }
    try { checkPacket(); } catch (e) {
      console.error('FAIL: frame ' + i + ' packet malformed: ' + e.message);
      process.exit(1);
    }
    packetBytes += Module._cogolf_packet_len();
  }
  if (replayPath === 'canary' && Module.HEAPU8.length > 1024 * 1024 * 1024) {
    console.error('FAIL: canary heap grew to ' + Module.HEAPU8.length + ' bytes');
    process.exit(1);
  }
  clearTimeout(watchdog);
  console.log('ok: loaded ' + path.basename(replayPath) + ' (board ' + chrome.board.w + 'x' +
    chrome.board.h + ' @ ' + chrome.board.tile + ' px/tile, ' + steps + ' beats), rendered ' +
    frameBudget + ' frames (' + packetBytes + ' packet bytes, heap ' +
    Math.round(Module.HEAPU8.length / 1024 / 1024) + ' MB)');
  process.exit(0);
}

// The bundle is injected with `Module` as a function parameter — a plain
// require() cannot configure it: the emitted `var Module` declaration
// hoists over any global we set.
const bundlePath = path.join(distDir, 'cogolf_replay.js');
new Function('Module', 'require', '__filename', '__dirname',
  fs.readFileSync(bundlePath, 'utf8'))(Module, require, bundlePath, distDir);
