# Working in this repo

cogame-cogolf is a two-seat, zero-sum adversarial-programming game as a
Coworld. Design and contract docs: `docs/RULES.md` (the game), `docs/PROTOCOL.md`
(wire protocol + results), `docs/REPLAY.md` (replay JSON + viewer contract), and
the manifest template `coworld_manifest_template.json` (the platform contract).

## Inviolable rules

1. **Closed schemas stay in triple sync.** `server/cogame_cogolf/results.py`
   (`results_doc` keys + the `reason` enum) == manifest `results_schema` ==
   `tools/ci/docker_smoke.sh` expectations. `tests/test_manifest.py` is the
   tripwire; never weaken it.
2. **Degrade, never hang.** Every wait is bounded: the connect timeout, the hole
   deadline, its single retry, the sandbox per-call CPU cap and per-batch wall
   cap, and the engine's wall-clock hard stop. A bad or missing submission is a
   scripted fallback, never a crash and never a forfeited hole. Play settles
   inside 60 % of `episodeTimeoutSeconds`.
3. **One parallel batch per hole.** Cogolf is a simultaneous-decision game: both
   seats' observations go out before either reply is awaited. A seat-by-seat
   engine doubles the wall clock and breaks the budget.
4. **Broadcast `done` before writing artifacts**; write results and replay
   independently, aggregate errors, and keep `/healthz` + `/global` answering
   for the 20 s shutdown grace before exiting 0.
5. **`num_agents` is 2 everywhere** — the schema, every variant, the
   certification fixture. There is no other seat count.
6. **Truncate on RUNE boundaries.** Every string that lands in the replay is
   decoded once, capped as a `str`, stripped of control characters and lone
   surrogates, and only then re-encoded. A byte-boundary truncation makes the
   replay unparseable by a strict reader.
7. **The replay is the viewer's only input.** Names, aliases, config, seed, deck
   version, every hole's spec and both seats' data, the beat stream and the
   result all live inside the document. The viewer is the STATIC wasm bundle —
   never a `/client/replay` pod.
8. **`GAME_VERSION` is a claim, not a counter.** `version.py` holds it with a
   prepend-only changelog in the shape `GVnn (short rule name): HEADLINE`.
   Anything that changes what a policy observes or how a seat is scored bumps it
   in the same commit.
9. **Wire strings live in one zero-import module.** `contract.py` (stdlib only)
   hoists every message type / key / enum a policy reads;
   `tests/contract_manifest.txt` is its golden copy. Renaming anything there is
   a four-surface change: contract.py, the manifest txt, docs/PROTOCOL.md, and
   `players/`. The failure this prevents is SILENT.
10. **No Factorio/Wube art, and no external engine.** The board art is drawn
    deterministically by `viewer/tools/build_atlas.py` plus the nano-banana cog
    renders in `viewer/art/`; the engine is the local sandbox, not a game
    server.

## Where things live

- `server/cogame_cogolf/` — `contract.py` (the wire), `config.py` (GameConfig ↔
  `config_schema`), `specs/` (the twelve-spec deck plus its registry),
  `values.py` (canon + the one equality rule), `sandbox.py` + `sandbox_runner.py`
  (the out-of-process code harness), `scoring.py`, `baseline.py`, `engine.py`
  (the hole loop), `replay.py`, `results.py`, `server.py` (aiohttp: `/player`,
  `/global`, `/client/*`, `/healthz`, replay mode), `uris.py`.
- `players/` — `main.py` (the env switch and the only entrypoint),
  `client.py` (the shared websocket harness), `llm_player.py`, `scripted.py`.
- Static wasm replay viewer: `replay-viewer/cogolf_replay.nim` + `config.nims`
  (Nim → emscripten; the arena as Bitworld sprite packets), `client/`
  (`replay_broadcast.html` = the cogame-factorio page plus an appended cogolf
  block, `broadcast_core.js` and `chrome_common.js` byte-for-byte from the
  starter, `static_replay*.js` worker glue, `replay_doc.js`), `viewer/assets/`
  (the atlas), `viewer/art/` (the split cog renders), `scripts/art/` (the source
  sheet + the split script). `viewer/build_viewer.sh` builds into `viewer/dist/`;
  `tools/build_replay_viewer.sh` is the `coworld build` hook;
  `tools/wasm_replay_smoke.cjs` is the node smoke.
- `tests/` — the offline pytest suite. `COGAME_REQUIRE_WASM_BUILD=1` turns the
  viewer's build-dependent skips into failures (CI sets it in `wasm-viewer`).

## Build / test / package

```sh
uv sync
uv run pytest
python3 viewer/tools/build_atlas.py         # -> viewer/assets/atlas.{png,json}
bash viewer/build_viewer.sh                 # -> viewer/dist (nim 2.2.x + emcc)
docker build --platform=linux/amd64 -t cogame-cogolf:local .
./tools/ci/docker_smoke.sh cogame-cogolf:local
```

## Workflows that are easy to get wrong

- **Releasing.** `coworld-release.yml` only: build → certify → upload policies →
  `upload-coworld` → `secret put`. The order is load-bearing (`upload-policy`
  needs the local image, `secret put` needs the Coworld to exist).
- **The policy image.** `compose.yaml` builds two images; the policies in
  `tools/ci/policies.json` carry `"image": "cogame-cogolf-player:latest"`
  explicitly, because the local `<IMAGE>` tag CI builds is the game image.
- **Fixtures.** The certification fixture pins EVERY field its ending depends on
  (deck, holes, seed, deadlines, wall clock), and it is sized so the replay
  outlasts the viewer smoke's `--soak` window.
- **Specs.** A new spec module must satisfy `tests/test_specs.py`: its reference
  passes its own examples and par tests, and its two baseline implementations
  diverge from the reference on DIFFERENT clauses — that is what makes a
  scripted-vs-scripted episode a real contest instead of a null match.
