"""Static wasm replay viewer checks (replay-viewer/, client/, viewer/,
tools/build_replay_viewer.sh).

Layers, cheapest first:

- fixture contract: tests/fixtures/sample_replay.json is a real episode of
  the current writer and parses as the viewer's ReplayDoc does;
- page contract: client/replay_broadcast.html (shipped as dist/index.html)
  reads the `replay` query parameter, falls back to /replay-data, references
  bundle assets relatively only, sets data-replay-loaded / data-replay-error,
  keeps the inherited cogame-factorio chrome, drops the starter elements the
  design note removed and carries the appended cogolf ones;
- transport contract: --band / --hudscale on :root, nothing overlaid on the
  transport band, the end card stops above it, every seek dismisses it, and
  the scrubber beats are labelled clickable buttons with CSS for every kind;
- build hook: tools/build_replay_viewer.sh asserts every bundle file;
- sprite atlas: viewer/assets/atlas.{png,json} exist, the manifest is
  well-formed, and the two seat characters are nano-banana cog renders;
- build outputs + wasm smoke: viewer/dist/{index.html, cogolf_replay.js,
  cogolf_replay.wasm, cogolf_replay.data, static_replay.js,
  static_replay_worker.js, broadcast_core.js, chrome_common.js,
  replay_doc.js} exist after viewer/build_viewer.sh, and
  tools/wasm_replay_smoke.cjs loads the EXACT emitted module under node
  against the sample replay plus an address-space canary. Those skip when
  the wasm build is absent unless COGAME_REQUIRE_WASM_BUILD=1, which CI
  sets in the wasm-viewer job.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
VIEWER_DIR = REPO_ROOT / "viewer"
VIEWER_DIST = VIEWER_DIR / "dist"
CLIENT_DIR = REPO_ROOT / "client"
PAGE = CLIENT_DIR / "replay_broadcast.html"
ASSETS = VIEWER_DIR / "assets"
ART = VIEWER_DIR / "art"
FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = FIXTURES / "sample_replay.json"
SMOKE = REPO_ROOT / "tools" / "wasm_replay_smoke.cjs"

BUNDLE_FILES = ("index.html", "cogolf_replay.js", "cogolf_replay.wasm",
                "cogolf_replay.data", "static_replay.js",
                "static_replay_worker.js", "broadcast_core.js",
                "chrome_common.js", "replay_doc.js")
NOT_BUILT = "viewer not built - run viewer/build_viewer.sh first"


def _index_html() -> str:
    return PAGE.read_text()


def _node() -> str | None:
    return shutil.which("node")


def _fn_body(html: str, name: str) -> str:
    """The source of one top-level function of the page's inline script."""
    start = html.index(f"function {name}(")
    depth = 0
    for i in range(html.index("{", start), len(html)):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                return html[start:i + 1]
    raise AssertionError(f"unterminated function {name}")


# --------------------------------------------------------------------------
# fixture
# --------------------------------------------------------------------------

def test_the_sample_replay_is_a_real_current_format_episode():
    doc = json.loads(SAMPLE.read_bytes().decode("utf-8"))
    assert doc["format"] == "cogame-cogolf-replay" and doc["version"] == 1
    assert len(doc["names"]) == 2 and doc["aliases"] == ["Ash", "Basil"]
    assert len(doc["holes"]) == 3
    assert len(doc["events"]) > 40
    assert doc["result"]["scores"][0] + doc["result"]["scores"][1] == 0
    assert doc["result"]["killer_test"]


@pytest.mark.skipif(_node() is None, reason="node not installed")
def test_replay_doc_js_validates_the_sample_under_node():
    script = f"""
const RD = require({str(CLIENT_DIR / "replay_doc.js")!r});
const fs = require('fs');
const doc = RD.parseReplay(fs.readFileSync({str(SAMPLE)!r}, 'utf8'));
const killer = RD.killerBeat(doc);
if (killer < 0) throw new Error('killer beat not found');
const kinds = new Set();
doc.events.forEach((ev, i) => {{
  const k = RD.markerKind(ev, i, killer);
  if (k) kinds.add(k);
  const text = RD.beatText(doc, ev);
  if (!text) throw new Error('empty beat text for ' + ev.kind);
}});
const state = RD.stateAt(doc, doc.events.length - 1);
if (!state.done) throw new Error('last beat is not the end');
const ro = RD.seatReadout(RD.stateAt(doc, Math.floor(doc.events.length / 2)), 0);
console.log(JSON.stringify({{kinds: [...kinds], shots: ro.shots}}));
"""
    proc = subprocess.run([_node(), "-e", script], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = json.loads(proc.stdout)
    assert {"hole", "breach", "killer"} <= set(out["kinds"])
    assert set(out["kinds"]) <= set(["hole", "breach", "illegal", "fallback",
                                     "killer"])


# --------------------------------------------------------------------------
# page contract
# --------------------------------------------------------------------------

def test_index_reads_replay_query_param_and_falls_back_to_replay_data():
    html = _index_html()
    assert re.search(r"URLSearchParams\(location\.search\)", html)
    assert re.search(r'\.get\("replay"\)', html)
    assert '"/replay-data"' in html, "container replay mode fallback"


def test_index_references_bundle_assets_relatively_only():
    html = _index_html()
    srcs = re.findall(r'<script[^>]+src="([^"]+)"', html)
    assert srcs, "no script tags"
    for src in srcs:
        assert src.startswith("./"), f"non-relative script src {src!r}"
    assert "./static_replay.js" in srcs and "./replay_doc.js" in srcs
    assert "./chrome_common.js" in srcs
    # the wasm runtime lives in the Worker, never on the main thread
    assert "./cogolf_replay.js" not in srcs and "./broadcast_core.js" not in srcs
    for m in re.finditer(r'(?:src|href)="([^"]+)"', html):
        url = m.group(1)
        assert not url.startswith(("http://", "https://", "//")), \
            f"remote asset {url!r}"
    assert "@import" not in html and "fonts.googleapis" not in html
    assert "CogolfStaticReplay.createCore(" in html


def test_the_load_signals_the_viewer_smoke_reads():
    """data-replay-loaded is set on the FIRST DRAWN FRAME (pixels
    composited), not when the bytes finished parsing; every failure sets
    data-replay-error."""
    js = (CLIENT_DIR / "static_replay.js").read_text()
    first_frame = js.index("message.type === 'firstFrame'")
    loaded = js.index("message.type === 'loaded'")
    marker = "setAttribute('data-replay-loaded', 'true')"
    assert marker in js
    assert first_frame < js.index(marker) < loaded, \
        "the loaded attribute must be set in the firstFrame branch"
    assert "setAttribute('data-replay-error'" in js
    assert js.count(marker) == 1


def test_the_pages_own_failure_paths_set_data_replay_error():
    """The other half of the negative signal. tools/ci/viewer_smoke.mjs fails
    fast on <html data-replay-error>; a failure that only paints the fail card
    (a bad ?replay= URL, a 404, a schema-invalid replay, the no-data and stuck
    timers) would otherwise report a generic 90 s timeout instead of its
    message. Every terminal failure path of the page sets the attribute, and a
    late frame that recovers the UI clears it again."""
    html = _index_html()
    setter = _fn_body(html, "setReplayError")
    assert 'documentElement.setAttribute("data-replay-error"' in setter
    for fn in ("showError", "showFailCard"):
        assert "setReplayError(" in _fn_body(html, fn), fn
    # every page-side failure funnels through those two writers
    catch = html[html.index("boot().catch("):]
    catch = catch[:catch.index("\n});")]
    assert "showError(" in catch and "showFailCard(" in catch
    assert 'showFailCard("Replay didn’t load"' in _fn_body(html, "noDataCard")
    assert 'showFailCard("Viewer stuck"' in _fn_body(html, "armStuckTimer")
    assert 'showFailCard("Board renderer failed"' in _fn_body(html, "startCore")
    assert 'showError("uncaught error"' in html
    assert 'showError("unhandled rejection"' in html
    # a late frame fully recovers the UI: the marker goes with the card
    assert 'removeAttribute("data-replay-error")' in \
        _fn_body(html, "clearFailCard")


def test_the_bootstrap_and_the_link_flags_are_the_matched_pair():
    """cogame-lantern, 2026-08-23: MODULARIZE link flags with a
    non-modularized bootstrap hangs on 'Loading replay…' forever. Both come
    from cogame-factorio here, and they must stay that pair."""
    worker = (CLIENT_DIR / "static_replay_worker.js").read_text()
    nims = (REPO_ROOT / "replay-viewer" / "config.nims").read_text()
    assert "var Module = {}" in worker
    assert "Module.onRuntimeInitialized" in worker
    assert "importScripts('./broadcast_core.js', './cogolf_replay.js')" in worker
    assert "MODULARIZE" not in nims and "EXPORT_NAME" not in nims
    for export in ("_cogolf_load_replay", "_cogolf_frame", "_cogolf_input",
                   "_cogolf_packet_ptr", "_cogolf_error_ptr",
                   "_cogolf_stage_ptr", "_cogolf_set_atlas"):
        assert export in nims, export
    assert "Module.onAbort" in worker and "_cogolf_stage_ptr" in worker
    assert "ABORTING_MALLOC" in nims


def test_index_surfaces_failures_and_shell_hooks():
    html = _index_html()
    assert 'id="banner"' in html and 'role="alert"' in html
    assert 'id="failcard"' in html and "Board renderer failed" in html
    assert 'role="status"' in html, "loading plate"
    assert "prefers-reduced-motion" in html
    chrome = (CLIENT_DIR / "chrome_common.js").read_text()
    assert "uiToggle('spoilers'" in chrome and "getSpoilers" in html
    assert 'src: "ctf-shell", type: "esc"' in html
    assert 'target = "_top"' in html
    assert "game_version" in html
    worker = (CLIENT_DIR / "static_replay_worker.js").read_text()
    assert "postMessage({ type: 'boot' })" in worker
    assert "onBoot: onWorkerBoot" in html and "armNoDataTimer(FAIL_STUCK_MS)" in html
    assert "clearFailCard()" in html and "timeoutCardShown && firstFrameSeen" in html
    assert "_cogolf_set_atlas" in worker and "createImageBitmap" in worker


def test_the_inherited_chrome_is_byte_for_byte_the_starters():
    """chrome_common.js and broadcast_core.js are copied verbatim from
    cogame-factorio; cogolf's beat semantics ride in through the existing
    ctx callbacks."""
    js = (CLIENT_DIR / "chrome_common.js").read_text()
    assert "window.ChromeCommon = function" in js
    for name in ("uiToggle", "stripSeatSuffix", "teamHeadline", "setName",
                 "esc", "renderTransport", "setMarkers", "setVerdict",
                 "getSpoilers", "setSpoilers"):
        assert name in js, name
    assert r"replace(/[\s_]*\(\d+\)\s*$/, '')" in js
    assert "import " not in js and "fetch(" not in js
    core = (CLIENT_DIR / "broadcast_core.js").read_text()
    assert "window.BroadcastCore" in core
    # neither file mentions cogolf: they are game-agnostic, unedited chrome
    assert "cogolf" not in js.lower() and "cogolf" not in core.lower()


def test_index_carries_the_inherited_transport_and_scorebug_dom():
    html = _index_html()
    for el_id in ("transport", "btn-play", "btn-restart", "btn-back", "btn-end",
                  "btn-spoilers", "speedchips", "scrub", "scrub-fill",
                  "scrub-head", "scrub-win", "scrub-hover", "tick-clock",
                  "win-chip", "scorebug", "seatchips", "clock-time",
                  "clock-caption", "stepro", "endcard", "ec-headline",
                  "ec-teams", "ec-replay", "loader", "failcard", "status",
                  "tooltip", "main", "col-r", "tab-r", "plaque-r", "result"):
        assert f'id="{el_id}"' in html, el_id
    for token in ("--paper:#f2e8d8", "--amber:#e8a33d", "--stage-lo:#16110d",
                  "--red:#e0523a", "--pixfont:'rajdhani'"):
        assert token in html, token
    assert "data:font/ttf;base64," in html
    assert (VIEWER_DIR / "FONT_LICENSE.txt").exists()


def test_the_starter_elements_the_design_note_removed_are_gone():
    html = _index_html()
    for gone in ("maptools", "tilepos", "zoom", "fit", "fitmap", "follow",
                 "charmark", "charmark-lbl", "legend", "legend-cols",
                 "inventory", "flows", "viewpanel", "minimap"):
        assert f'id="{gone}"' not in html, f"{gone} should be gone"
    for gone in ("fitBase(", "fitMap(", "setFollow(", "focusCharacter(",
                 "startCharGlide(", "renderLegend("):
        assert gone not in html, f"{gone} should be gone"
    # the camera keys go with the camera: the arena is fixed and always fits
    for gone in ('k === "f"', 'k === "g"', 'k === "c"', 'k === "z"'):
        assert gone not in html, gone


def test_the_appended_cogolf_block_is_present():
    html = _index_html()
    assert "cogolf additions to the inherited cogame-factorio chrome" in html
    assert '<style id="cogolf-css">' in html
    for el_id in ("scroll", "scroll-title", "scroll-prompt", "scroll-amb",
                  "feed", "cogolf-plaque", "spec-text", "spec-amb", "code",
                  "tests", "par-line", "ro-shots", "ro-breach", "ro-held",
                  "ro-illegal", "ro-par"):
        assert f'id="{el_id}"' in html, el_id
    # the beat is the timeline unit
    assert "BASE_BEAT_MS" in html and "C.SPEEDS" in html
    assert "selectBeat(" in html and "beatIdx" in html
    assert "core.sendCommand(`b:${beatIdx}`)" in html
    # the collapsible plaque survives the fork
    for needle in ('k === "p"', 'id="tab-r"', 'C.uiToggle("panes", true)',
                   "localStorage.setItem(PANES_KEY", "<b>p</b> pane"):
        assert needle in html, needle


def test_the_transport_rules():
    html = _index_html()
    # relayout() publishes --hudscale and --band on :root
    assert "function relayout()" in html
    assert 'setProperty("--hudscale"' in html and 'setProperty("--band"' in html
    assert 'ResizeObserver(() => relayout()).observe($("transport"))' in html
    # nothing is overlaid on the transport band: the end card stops above it
    assert "#endcard{inset:0 0 var(--band) 0}" in html
    # EVERY seek dismisses the end card
    assert re.search(r"function selectBeat\(i\) \{[^}]*hideEndCard\(\)", html,
                     re.DOTALL)
    # the scrubber beats are labelled, clickable buttons
    assert 'document.createElement("button")' in html
    assert 'b.setAttribute("aria-label", m.title)' in html
    assert "selectBeat(m.idx)" in html
    # CSS for every kind the writer emits
    for kind in ("hole", "breach", "illegal", "fallback", "killer"):
        assert f".beat-marker.{kind}{{" in html, kind
    # and for kinds it does NOT emit, no dead rules
    for gone in (".beat-marker.error{", ".beat-marker.noop{",
                 ".beat-marker.dead{"):
        assert gone not in html, gone


def test_legibility_at_360_px():
    """The featured-match iframe is ~360 px wide: names ellipsize instead of
    collapsing to '…', and every readout label is hidden under 640 px."""
    html = _index_html()
    assert ".plate-name{flex:1 1 auto; min-width:3.2em}" in html
    assert "@media (max-width: 640px){" in html
    assert re.search(r"@media \(max-width: 640px\)\{\s*\n?\s*\.ro \.k, "
                     r"\.wallsub, \.scrub-key", html)
    assert "@media (max-width: 720px){ #main{--col-r:var(--tab)}" in html
    assert '"nm plate-name"' in html


def test_the_two_name_spaces_are_both_rendered():
    """The alias is what the policies saw; the real player name is
    spectator-side only. The chip shows both."""
    html = _index_html()
    assert "const seatAlias = (i) =>" in html
    assert "const seatName = (i) =>" in html
    assert "seatAlias(r.i).toUpperCase()" in html


@pytest.mark.skipif(_node() is None, reason="node not installed")
def test_the_client_javascript_parses_under_node():
    for name in ("chrome_common.js", "replay_doc.js", "static_replay.js",
                 "static_replay_worker.js", "broadcast_core.js"):
        proc = subprocess.run([_node(), "--check", str(CLIENT_DIR / name)],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, name + ": " + proc.stdout + proc.stderr


@pytest.mark.skipif(_node() is None, reason="node not installed")
def test_the_pages_inline_script_parses_under_node(tmp_path):
    body = re.search(r"<script>\n(.*)\n</script>", _index_html(),
                     re.DOTALL).group(1)
    path = tmp_path / "page.js"
    path.write_text(body)
    proc = subprocess.run([_node(), "--check", str(path)], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --------------------------------------------------------------------------
# sprite atlas
# --------------------------------------------------------------------------

def test_the_atlas_manifest_shape():
    assert (ASSETS / "atlas.png").exists() and (ASSETS / "atlas.json").exists()
    assert (ASSETS / "atlas.png").stat().st_size < 200 * 1024, "atlas budget"
    manifest = json.loads((ASSETS / "atlas.json").read_text())
    assert manifest["tile_px"] == 32
    for key, idx in manifest["by_name"].items():
        entry = manifest["sprites"][idx]
        assert key == f'{entry["name"]}|{entry["dir"]}'
        assert entry["w"] > 0 and entry["h"] > 0
        assert 0 <= entry["cx"] <= entry["w"]
        assert 0 <= entry["cy"] <= entry["h"]
    names = manifest["by_name"]
    for seat in ("ash", "basil"):
        for kind in ("brick", "brick_cracked", "shield", "flag", "pennant",
                     "tee", "cog"):
            assert f"{kind}|{seat}" in names, f"{kind}|{seat}"
        assert f"dart|{seat}-east" in names and f"dart|{seat}-west" in names
    assert "dart|par" in names and "splash|" in names
    for part in ("left", "mid", "right"):
        assert f"parchment|{part}" in names
    assert "ground|grass-1" in names and "ground|stone-1" in names
    assert "debris|1" in names


def test_the_seat_characters_are_nano_banana_cog_renders():
    """The board art rule: the seat sprites are nano-banana renders of the
    Softmax cog, one kit per role, so the seats read at board scale without
    labels. The source sheet and the split script are committed."""
    sheet = REPO_ROOT / "scripts" / "art" / "source" / "cogs_sheet.png"
    split = REPO_ROOT / "scripts" / "art" / "split_cog_sheet.py"
    assert sheet.exists() and sheet.stat().st_size > 50 * 1024
    assert split.exists()
    assert (ART / "cog_ash.png").exists() and (ART / "cog_basil.png").exists()
    generator = (VIEWER_DIR / "tools" / "build_atlas.py").read_text()
    assert "nano-banana" in generator
    assert "does NOT own the two seat characters" in generator
    # no Factorio / Wube art anywhere
    assert not (ASSETS / "README.md").exists() or \
        "Wube" not in (ASSETS / "README.md").read_text()


# --------------------------------------------------------------------------
# build hook + the emitted bundle
# --------------------------------------------------------------------------

def test_build_replay_viewer_hook_asserts_every_bundle_file():
    hook = (REPO_ROOT / "tools" / "build_replay_viewer.sh").read_text()
    assert "--target wasm-builder" in hook
    assert "/workspace/viewer/dist/." in hook
    for name in BUNDLE_FILES:
        assert name in hook, f"the hook does not assert {name}"
    # the output parent is created BEFORE the containment check (cogame-ecos)
    assert hook.index('mkdir -p "$(dirname "${requested_output}")"') < \
        hook.index('output_parent="$(cd')
    assert os.access(REPO_ROOT / "tools" / "build_replay_viewer.sh", os.X_OK)
    assert os.access(VIEWER_DIR / "build_viewer.sh", os.X_OK)


def _skip_or_fail_not_built():
    if os.environ.get("COGAME_REQUIRE_WASM_BUILD"):
        pytest.fail(NOT_BUILT + " (COGAME_REQUIRE_WASM_BUILD is set)")
    pytest.skip(NOT_BUILT)


def test_build_viewer_outputs_exist():
    if not (VIEWER_DIST / "cogolf_replay.wasm").exists():
        _skip_or_fail_not_built()
    for name in BUNDLE_FILES:
        assert (VIEWER_DIST / name).exists(), f"viewer/dist/{name} missing"
    assert (VIEWER_DIST / "index.html").read_bytes() == PAGE.read_bytes()
    for name in ("chrome_common.js", "replay_doc.js", "static_replay.js",
                 "static_replay_worker.js", "broadcast_core.js"):
        assert (VIEWER_DIST / name).read_bytes() == \
            (CLIENT_DIR / name).read_bytes(), name
    js = (VIEWER_DIST / "cogolf_replay.js").read_text(errors="replace")
    for export in ("_cogolf_load_replay", "_cogolf_frame", "_cogolf_input",
                   "_cogolf_packet_ptr", "_cogolf_packet_len",
                   "_cogolf_error_ptr", "_cogolf_stage_ptr"):
        assert export in js, export
    # the sprite atlas rides the emscripten preload (.data), never a fetch
    assert (VIEWER_DIST / "cogolf_replay.data").stat().st_size > 10 * 1024


def _smoke(target: str, frames: int) -> subprocess.CompletedProcess:
    return subprocess.run([_node(), str(SMOKE), str(VIEWER_DIST), target,
                           str(frames)], capture_output=True, text=True,
                          timeout=180)


def test_wasm_smoke_renders_the_sample_replay():
    if not (VIEWER_DIST / "cogolf_replay.wasm").exists():
        _skip_or_fail_not_built()
    if _node() is None:
        if os.environ.get("COGAME_REQUIRE_WASM_BUILD"):
            pytest.fail("node is required for the wasm smoke")
        pytest.skip("node not installed")
    proc = _smoke(str(SAMPLE), 60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.startswith("ok: loaded sample_replay.json"), proc.stdout


def test_wasm_smoke_address_space_canary():
    if not (VIEWER_DIST / "cogolf_replay.wasm").exists():
        _skip_or_fail_not_built()
    if _node() is None:
        pytest.skip("node not installed")
    proc = _smoke("canary", 150)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ok: loaded canary" in proc.stdout, proc.stdout
