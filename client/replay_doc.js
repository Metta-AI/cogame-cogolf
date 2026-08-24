// cogame-cogolf replay viewer: replay document parsing + beat helpers.
//
// Loaded by client/replay_broadcast.html as a classic <script> (exposes
// window.ReplayDoc) and usable under node (module.exports). No DOM, no
// wasm: pure functions over the replay document (docs/REPLAY.md). The
// board itself is drawn by the Nim/wasm renderer (replay-viewer/) from the
// same bytes; this module is what the page's chrome, feed, scrubber and
// end card read.
//
// The timeline unit is a BEAT: one entry of `events[]`, in order.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.ReplayDoc = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const FORMAT = "cogame-cogolf-replay";
  const VERSION = 1;
  const SEATS = 2;

  const EVENT_KINDS = new Set([
    "hole_start", "submission", "test_verdict", "par_result", "hole_score",
    "episode_end",
  ]);
  // The five scrubber beat kinds the page has CSS for.
  const MARKER_KINDS = ["hole", "breach", "illegal", "fallback", "killer"];

  function fail(msg) { throw new Error("replay: " + msg); }
  const isNum = (v) => typeof v === "number" && Number.isFinite(v);
  const isInt = (v) => Number.isInteger(v);

  // Structural validation of the parts the viewer touches. Throws with a
  // path-ish message; returns the doc for chaining.
  function validateReplay(doc) {
    if (!doc || typeof doc !== "object") fail("not an object");
    if (doc.format !== FORMAT) fail(`format is ${JSON.stringify(doc.format)}, expected ${FORMAT}`);
    if (doc.version !== VERSION) fail(`version is ${doc.version}, expected ${VERSION}`);
    if (!Array.isArray(doc.names) || doc.names.length !== SEATS) fail("names must hold two seats");
    if (!Array.isArray(doc.aliases) || doc.aliases.length !== SEATS) fail("aliases must hold two seats");
    if (!doc.config || typeof doc.config !== "object") fail("config missing");
    if (!isInt(doc.seed)) fail("seed missing");
    if (typeof doc.deck_version !== "string") fail("deck_version missing");
    if (!Array.isArray(doc.holes)) fail("holes missing");
    doc.holes.forEach((hole, hi) => {
      const p = `holes[${hi}]`;
      if (!isInt(hole.hole)) fail(`${p}.hole not an integer`);
      if (!hole.spec || typeof hole.spec.prompt !== "string") fail(`${p}.spec malformed`);
      if (!Array.isArray(hole.seats) || hole.seats.length !== SEATS) fail(`${p}.seats malformed`);
      hole.seats.forEach((seat, si) => {
        const q = `${p}.seats[${si}]`;
        if (seat.slot !== si) fail(`${q}.slot is ${seat.slot}, expected ${si}`);
        if (typeof seat.impl !== "string") fail(`${q}.impl not a string`);
        if (!Array.isArray(seat.tests)) fail(`${q}.tests missing`);
        if (!isInt(seat.par_fails)) fail(`${q}.par_fails not an integer`);
      });
      if (!Array.isArray(hole.hole_score) || hole.hole_score.length !== SEATS) fail(`${p}.hole_score malformed`);
      if (!Array.isArray(hole.cumulative) || hole.cumulative.length !== SEATS) fail(`${p}.cumulative malformed`);
    });
    if (!Array.isArray(doc.events) || !doc.events.length) fail("events missing");
    doc.events.forEach((ev, i) => {
      if (!ev || !EVENT_KINDS.has(ev.kind)) fail(`events[${i}].kind is ${JSON.stringify(ev && ev.kind)}`);
    });
    if (!doc.result || typeof doc.result !== "object") fail("result missing");
    if (!Array.isArray(doc.result.scores) || doc.result.scores.length !== SEATS) fail("result.scores malformed");
    return doc;
  }

  function parseReplay(text) {
    let doc;
    try { doc = JSON.parse(text); }
    catch (e) { fail("invalid JSON: " + e.message); }
    return validateReplay(doc);
  }

  // ---- beats ---------------------------------------------------------------

  function killerBeat(doc) {
    // The index of the beat the endcard's killer test was fired on.
    const k = doc.result && doc.result.killer_test;
    if (!k) return -1;
    return doc.events.findIndex((ev) => ev.kind === "test_verdict" &&
      ev.hole === k.hole && ev.slot === k.slot && ev.name === k.name);
  }

  // The scrubber kind of a beat, or null when it gets no marker.
  function markerKind(ev, index, killerIndex) {
    if (index === killerIndex) return "killer";
    if (ev.kind === "hole_start") return "hole";
    if (ev.kind === "submission" && ev.fallback) return "fallback";
    if (ev.kind === "test_verdict") {
      if (ev.outcome === "breach") return "breach";
      if (ev.outcome === "illegal") return "illegal";
    }
    return null;
  }

  function alias(doc, slot) { return doc.aliases[slot] || `seat ${slot}`; }

  // One line of the feed / one aria-label for a scrubber beat.
  function beatText(doc, ev) {
    const h = ev.hole ? `H${ev.hole}` : "";
    switch (ev.kind) {
      case "hole_start":
        return `${h} · ${ev.title || ev.spec_key} — ${ev.prompt_head || ""}`;
      case "submission":
        return `${h} · ${alias(doc, ev.slot)} tees up — ${ev.impl_lines} lines, ` +
          `${ev.test_count} tests` +
          (ev.fallback ? ` (FALLBACK: ${ev.fallback.cause})` : "");
      case "test_verdict": {
        const who = alias(doc, ev.slot);
        const them = alias(doc, ev.target_slot);
        const name = JSON.stringify(ev.name);
        if (ev.outcome === "breach") return `${h} · ${who} ▸ ${name} — BREACH (${them} returned ${ev.observed})`;
        if (ev.outcome === "held") return `${h} · ${who} ▸ ${name} — held by ${them}`;
        return `${h} · ${who} ▸ ${name} — ILLEGAL (${ev.legal_reason})`;
      }
      case "par_result":
        return `${h} · audit of ${alias(doc, ev.slot)} — ${ev.par_fails}/${ev.par_total} failed`;
      case "hole_score":
        return `${h} · hole score ${ev.score[0] >= 0 ? "+" : ""}${ev.score[0]} / ` +
          `${ev.score[1] >= 0 ? "+" : ""}${ev.score[1]} — running ${ev.cumulative.join(" : ")}`;
      case "episode_end":
        return `MATCH OVER — ${ev.reason} — ${ev.scores.join(" : ")}`;
      default:
        return ev.kind;
    }
  }

  // ---- the board state at a beat ------------------------------------------
  // Everything the chrome shows is derived here, so the page never has to
  // remember anything between seeks.
  function stateAt(doc, index) {
    const state = {
      beat: Math.max(0, Math.min(index, doc.events.length - 1)),
      hole: 0,
      holeIndex: -1,
      specKey: "",
      title: "",
      cumulative: [0, 0],
      shots: [[], []],           // this hole's verdicts, per seat
      par: [null, null],         // this hole's par fails, per seat
      fallback: [null, null],
      done: false,
    };
    for (let i = 0; i <= state.beat; i++) {
      const ev = doc.events[i];
      switch (ev.kind) {
        case "hole_start":
          state.hole = ev.hole;
          state.holeIndex = doc.holes.findIndex((h) => h.hole === ev.hole);
          state.specKey = ev.spec_key || "";
          state.title = ev.title || "";
          state.shots = [[], []];
          state.par = [null, null];
          state.fallback = [null, null];
          break;
        case "submission":
          state.fallback[ev.slot] = ev.fallback || null;
          break;
        case "test_verdict":
          state.shots[ev.slot].push(ev);
          break;
        case "par_result":
          state.par[ev.slot] = ev.par_fails;
          break;
        case "hole_score":
          state.cumulative = ev.cumulative.slice();
          break;
        case "episode_end":
          state.done = true;
          break;
        default:
          break;
      }
    }
    return state;
  }

  // The selected seat's readout for the current hole.
  function seatReadout(state, slot) {
    const shots = state.shots[slot] || [];
    return {
      shots: shots.length,
      breach: shots.filter((s) => s.outcome === "breach").length,
      held: shots.filter((s) => s.outcome === "held").length,
      illegal: shots.filter((s) => s.outcome === "illegal").length,
      par: state.par[slot],
    };
  }

  return {
    FORMAT, VERSION, SEATS, EVENT_KINDS, MARKER_KINDS,
    validateReplay, parseReplay, markerKind, killerBeat, beatText, stateAt,
    seatReadout, alias,
  };
});
