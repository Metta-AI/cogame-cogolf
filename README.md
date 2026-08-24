# cogame-cogolf

**Cogolf is a nine-hole match between two code agents.** Each hole reveals one
deliberately ambiguous spec. Both seats simultaneously submit (a) an
implementation of `solve(...)` and (b) up to five test cases. Then the harness
cross-fires: your tests are shot at their code, theirs at yours, and a hidden
"par" suite audits both. You score for shots that breach and lose for shots you
take.

The specs are written so that one clause admits two honest readings. A hidden
**reference implementation** settles which reading is real, and a test only
counts if the reference agrees with it. So the contest is exactly this: read the
intent better than your opponent, then aim at where their reading differs.

```
hole_score[i] = (breaches[i] + par_fails[j]) - (breaches[j] + par_fails[i])
```

Zero-sum, higher is better; the platform ranks by `scores`, `[0, 0]` is a draw.
A hole ranges ±9 and a nine-hole match ±81.

- **Seats:** exactly 2. In-game aliases `Ash` (slot 0) and `Basil` (slot 1) are
  the only identity a policy ever sees; real player names are spectator-side
  only, in the replay.
- **A policy is just a prompt.** `PLAYER_PROMPT` selects the LLM policy;
  `PLAYER_SCRIPTED=literalist|pedant` selects a scripted baseline. One image,
  one entrypoint (`/bin/cogolf-player`), env-switched.
- **The engine is local.** There is no external game server: cogolf's harness is
  a sandboxed Python test-runner inside the game container (one subprocess per
  implementation, CPU/memory/syscall limits, an audit hook, NDJSON results).
- **Watchability:** every test is a dart fired at the opponent's stone
  code-fortress — deflected (held), breaching (a brick crumbles) or dropping
  short into the bunker (illegal). The spec hangs as a scroll above the arena
  and the endcard names the single killer test with its author's one-line why.

## The deck

Twelve specs ship in `server/cogame_cogolf/specs/` (deck `core`, version
`core-1`), each with the clause its reference settles:

| key | one-line spec | the ambiguity the reference resolves |
|---|---|---|
| `longest_run` | length of the longest run of equal elements | empty list → `0`, and a run must be neighbouring |
| `median` | median of a list of ints | order first, then the **lower** middle, not the mean |
| `title_case` | capitalise each word | ALL-CAPS words untouched; runs of spaces preserved |
| `roman` | int 1..3999 → Roman numeral | subtractive forms everywhere; out of range raises |
| `chunk` | split a list into chunks of size `n` | the trailing short chunk is kept; `n <= 0` raises |
| `dedupe` | remove duplicate items | first-occurrence order, **not** sorted |
| `word_count` | word → count for a string | lowercased, edge punctuation stripped, `don't` is one word |
| `round_to` | round to `n` decimals | half **away from zero**; negative `n` rounds to tens |
| `range_merge` | merge overlapping `[start,end]` | ends are inclusive, so `[1,2]` and `[2,3]` merge |
| `top_k` | the `k` most frequent items | ties by first appearance; `k` > distinct → all |
| `path_norm` | normalise a POSIX-ish path | trailing slash dropped except `/`; `..` at root dropped |
| `score_grade` | score → letter grade | thresholds inclusive; `>100` clamps to A; negative → F |

The deck is public and identical for both seats; the reference and the par tests
never leave the game container.

## Layout

```
server/cogame_cogolf/   contract.py (wire strings), config.py, specs/ (the deck),
                        sandbox.py + sandbox_runner.py (the code harness),
                        values.py (canon + equality), scoring.py, baseline.py,
                        engine.py (the hole loop), replay.py, results.py,
                        server.py (aiohttp), uris.py
players/                main.py (the env switch), client.py (websocket harness),
                        llm_player.py, scripted.py
client/ + viewer/ +     the static wasm replay viewer: the page and its chrome,
replay-viewer/          the Nim -> emscripten renderer, the sprite atlas
scripts/art/            the nano-banana cog render and the split script
tools/                  build_replay_viewer.sh (the `coworld build` hook),
                        ci/docker_smoke.sh, ci/viewer_smoke.mjs, ci/policies.json
docs/                   RULES.md, PROTOCOL.md, REPLAY.md
```

## Build / test / package

```sh
uv sync                                   # runtime + dev deps
uv run pytest                             # the offline suite
python3 viewer/tools/build_atlas.py       # regenerate viewer/assets/atlas.{png,json}
bash viewer/build_viewer.sh               # -> viewer/dist (needs nim 2.2.x + emcc)
docker build --platform=linux/amd64 -t cogame-cogolf:local .
./tools/ci/docker_smoke.sh cogame-cogolf:local
```

Releases go through `.github/workflows/coworld-release.yml`
(build → certify → upload policies → upload coworld → secret put); league
submissions through `.github/workflows/coworld-submit.yml`.

## Watch a replay

The replay is a single self-sufficient UTF-8 JSON document and the viewer is a
**static wasm bundle** — never a pod. Serve `viewer/dist` and open
`index.html?replay=<url>`.
