# Replay format — `cogame-cogolf-replay` v1

One UTF-8 JSON document written once at the end of the episode to
`COGAME_SAVE_REPLAY_URI` (plus a best-effort partial write on a harness fault).
It is the viewer's **only** input: the viewer fetches nothing but this file.

```jsonc
{
  "format": "cogame-cogolf-replay",
  "version": 1,
  "game_version": "GV01",
  "protocol": "cogame.cogolf.v1",
  "config": { /* the resolved GameConfig; tokens are EXCLUDED */ },
  "seed": 1234567,
  "deck": "core",
  "deck_version": "core-1",
  "names":   ["daveey", "daveey-1"],   // real players — spectator side only
  "aliases": ["Ash", "Basil"],         // what the policies saw
  "holes": [ /* one entry per resolved hole, below */ ],
  "events": [ /* the beat stream, below */ ],
  "result": { /* identical to COGAME_RESULTS_URI */ }
}
```

## A hole

```jsonc
{
  "hole": 1,
  "spec": {"key": "range_merge", "title": "Merge ranges", "prompt": "…",
           "signature": {…}, "examples": […],
           "ambiguity": "Ends are inclusive: [1,2] and [2,3] merge."},
  "seats": [
    {"slot": 0, "impl": "def solve(rs):\n    …", "impl_lines": 14,
     "broken": false, "broken_reason": null,
     "note": "treating ends as inclusive", "fallback": null,
     "dropped_tests": 0,
     "tests": [{"idx": 0, "name": "touching", "args": [[[1,2],[2,3]]],
                "expect": [[1,3]], "why": "spec says ranges include both ends",
                "legal": true, "legal_reason": null,
                "outcome": "breach", "observed": "[[1, 2], [2, 3]]"}],
     "par_fails": 1, "par_total": 4},
    {"slot": 1, "…": "…"}
  ],
  "hole_score": [3, -3],
  "cumulative": [3, -3]
}
```

`spec.ambiguity` is **replay only** — a one-line spectator note about what the
reference decided. It is never sent to a seat.

## The beat stream

`events[]` is one array, in chronological order, and **every event is exactly
one beat** — the viewer's timeline unit. The page seeks by beat index and tells
the wasm renderer with `b:<beat>`.

| `kind` | fields | drawn as |
|---|---|---|
| `hole_start` | `hole`, `spec_key`, `title`, `prompt_head` (≤160 chars) | the scroll unfurls; both fortresses rebuild to 9 bricks |
| `submission` | `hole`, `slot`, `impl_lines`, `impl_chars`, `test_count`, `note`, `fallback` | the seat tees up; a fallback tees up in grey with a `FALLBACK` chip |
| `test_verdict` | `hole`, `slot`, `target_slot`, `idx`, `name`, `args`, `expect`, `why`, `legal`, `legal_reason`, `outcome` (`breach`\|`held`\|`illegal`), `observed` | a dart flies tee → fortress: breach = a brick crumbles with a red flash; held = a shield ring; illegal = the dart drops into the sand bunker |
| `par_result` | `hole`, `slot`, `par_fails`, `par_total` | four grey audit darts fall from the scroll onto the fortress |
| `hole_score` | `hole`, `score` `[s0,s1]`, `cumulative` `[c0,c1]` | the pin flags rise and fall; the hole banner flips |
| `episode_end` | `reason`, `scores`, `killer_test` | the endcard |

A nine-hole match is ≈130 beats and ≈120 KB of JSON.

Scrubber marker kinds derived from the stream (each a labelled, clickable
button): `hole`, `breach`, `illegal`, `fallback`, and `killer` for the one beat
that fired the endcard's killer test.

## The result document

Identical to `COGAME_RESULTS_URI`; a CLOSED schema, kept in triple sync with the
manifest's `results_schema` and `tools/ci/docker_smoke.sh`
(`tests/test_manifest.py` is the tripwire):

`names`, `aliases`, `scores`, `hole_scores`, `breaches`, `breaches_taken`,
`par_fails`, `tests_fired`, `illegal_tests`, `holes_played`, `fallbacks`,
`fallback_causes`, `reason`, `wall_clock_seconds`, `seed`, `deck_version`,
`killer_test`.

`reason` is one of `complete`, `deadline`, `harness_fault`.

## Encoding

Every string in the replay has been through one sanitiser: lone surrogates
become `U+FFFD`, control characters other than `\n` and `\t` are stripped, and
every truncation happened on a **rune (Unicode code point) boundary, never a
byte**. The bytes therefore always parse under a strict UTF-8 JSON reader —
`json.loads(replay_bytes.decode("utf-8"))` with no error handler, which is what
`tests/test_replay.py` asserts on a replay full of emoji, CJK, a lone surrogate
and strings sitting exactly on every cap.

## The viewer

The replay is rendered by a **static wasm bundle** (`replay-viewer/` →
`viewer/dist`), never by a pod. Everything it needs is in these bytes: names,
aliases, config, seed, deck version, every hole's spec text and both seats'
submissions and verdicts, the whole beat stream and the result. The manifest
declares `"replay_viewer": {"bundle": "static-replay-viewer"}` and
`tools/build_replay_viewer.sh` is the `coworld build` hook that emits it.
