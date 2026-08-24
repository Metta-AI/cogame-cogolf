#!/usr/bin/env bash
# Build the static wasm replay viewer into viewer/dist:
#
#   index.html                 board page (client/replay_broadcast.html)
#   cogolf_replay.{js,wasm,data}   Nim -> emscripten renderer (replay-viewer/)
#                              + preloaded viewer/assets (cogolf sprite atlas)
#   static_replay.js, static_replay_worker.js   page <-> Worker glue
#   broadcast_core.js          Bitworld sprite-protocol compositor (verbatim)
#   chrome_common.js           shared replay chrome (verbatim)
#   replay_doc.js              replay document parsing for the page
#
# Runs locally (nim + emcc on PATH, packages synced with
# `nimby --global sync nimby.lock`) and inside the Dockerfile's wasm-builder
# stage (cwd = repo root). Ends with the same test -f / negative-grep guard
# chain style as cogame-factorio's so a half-built bundle never ships. All
# four viewer files (config.nims, the wasm entry .nim, static_replay*.js and
# index.html) come from ONE starter, cogame-factorio: the emscripten link
# flags (NO MODULARIZE, no EXPORT_NAME) and the JS bootstrap
# (`var Module = {}` + onRuntimeInitialized + importScripts) are a matched
# pair, and a mixture hangs on "Loading replay..." forever.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

NIM="${NIM:-nim}"
if ! command -v "$NIM" >/dev/null 2>&1; then
    if [ -x "$HOME/.nimby/nim/bin/nim" ]; then NIM="$HOME/.nimby/nim/bin/nim";
    else echo "error: nim not found on PATH (nimby use 2.2.4)" >&2; exit 1; fi
fi
if ! command -v emcc >/dev/null 2>&1; then
    echo "error: emcc not found on PATH - install emscripten (brew install emscripten)" >&2
    exit 1
fi
if [ ! -f viewer/assets/atlas.png ] || [ ! -f viewer/assets/atlas.json ]; then
    echo "error: viewer/assets/atlas.{png,json} missing (see viewer/tools/build_atlas.py)" >&2
    exit 1
fi

DIST=viewer/dist
mkdir -p "$DIST"
rm -f "$DIST"/*.js "$DIST"/*.wasm "$DIST"/*.data "$DIST"/*.html
rm -rf replay-viewer/dist

"$NIM" c --hints:off -d:emscripten replay-viewer/cogolf_replay.nim
cp replay-viewer/dist/cogolf_replay.js replay-viewer/dist/cogolf_replay.wasm \
   replay-viewer/dist/cogolf_replay.data "$DIST"/
rm -rf replay-viewer/dist/nimcache
cp client/broadcast_core.js "$DIST"/broadcast_core.js
cp client/chrome_common.js "$DIST"/chrome_common.js
cp client/replay_doc.js "$DIST"/replay_doc.js
cp client/static_replay.js "$DIST"/static_replay.js
cp client/static_replay_worker.js "$DIST"/static_replay_worker.js
cp client/replay_broadcast.html "$DIST"/index.html

# Guard chain (ctf style): every file the page loads, the wiring between them,
# and the things that must NOT be there.
test -f "$DIST"/cogolf_replay.wasm
test -f "$DIST"/cogolf_replay.js
test -f "$DIST"/cogolf_replay.data
test -f "$DIST"/static_replay_worker.js
test -f "$DIST"/index.html
test -s "$DIST"/chrome_common.js
grep -q 'window.ChromeCommon' "$DIST"/chrome_common.js
grep -q 'chrome_common.js' "$DIST"/index.html
test -s "$DIST"/broadcast_core.js
grep -q 'window.BroadcastCore' "$DIST"/broadcast_core.js
grep -q 'window.ReplayDoc\|root.ReplayDoc' "$DIST"/replay_doc.js
grep -q 'replay_doc.js' "$DIST"/index.html
grep -q 'static_replay.js' "$DIST"/index.html
grep -q 'static_replay_worker.js' "$DIST"/static_replay.js
grep -q "importScripts('./broadcast_core.js', './cogolf_replay.js')" "$DIST"/static_replay_worker.js
grep -q '_cogolf_load_replay' "$DIST"/cogolf_replay.js
grep -q '_cogolf_stage_ptr' "$DIST"/cogolf_replay.js
grep -q '_cogolf_set_atlas' "$DIST"/cogolf_replay.js
# The bootstrap and the link flags are the NON-modularized pair.
grep -q 'Module.onRuntimeInitialized' "$DIST"/static_replay_worker.js
! grep -q 'EXPORT_NAME' "$DIST"/cogolf_replay.js
# The load signal the viewer smoke reads.
grep -q "setAttribute('data-replay-loaded', 'true')" "$DIST"/static_replay.js
grep -q "setAttribute('data-replay-error'" "$DIST"/static_replay.js
# The page must fetch the replay itself (?replay= / /replay-data) and never
# load the runtime on the main thread.
grep -q 'params.get("replay")' "$DIST"/index.html
! grep -q '<script src="./broadcast_core.js"></script>' "$DIST"/index.html
! grep -q '<script src="./cogolf_replay.js"></script>' "$DIST"/index.html
! grep -q 'viewer.js' "$DIST"/index.html
! grep -q 'replay_pack.js' "$DIST"/index.html
# The starter elements cogolf removed must not come back.
! grep -q 'id="maptools"' "$DIST"/index.html
! grep -q 'id="charmark"' "$DIST"/index.html
! grep -q 'id="legend"' "$DIST"/index.html
! grep -q 'id="viewpanel"' "$DIST"/index.html
# Relative asset paths only (the bundle is served under /client/replay/ and
# from the Observatory's own host).
! grep -Eq 'src="/[^/]' "$DIST"/index.html

ls -la "$DIST"
echo "build_viewer: OK"
