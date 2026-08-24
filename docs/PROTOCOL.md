# cogame-cogolf — wire protocol `cogame.cogolf.v1`

Transport: one websocket per seat, `GET /player?slot=N&token=T`, one JSON text
message per hole each way. Every wire string in this document comes from
`server/cogame_cogolf/contract.py`; renaming one there is a four-surface change
(contract.py, `tests/contract_manifest.txt`, this page, `players/`).

```
server -> player   welcome        once per (re)connection
server -> player   observation    one per hole, re-sent once with retry: true
player -> server   submission     the reply
server -> player   done           episode end; the player process exits 0
```

Routes:

| route | what it is |
|---|---|
| `GET /healthz` | `{"status": "ok"}` |
| `GET /player?slot=N&token=T` | the seat websocket (403 bad slot/token, 409 already connected) |
| `GET /global` | broadcast-only spectator websocket |
| `GET /client/global`, `GET /client/player?slot=N&token=T` | real token-checked HTML pages; neither opens a player socket |
| `GET /replay-data`, `/client/replay/` | replay mode only (`COGAME_LOAD_REPLAY_URI`) |

Runtime contract (environment): `COGAME_CONFIG_URI`, `COGAME_RESULTS_URI`,
`COGAME_SAVE_REPLAY_URI`, `COGAME_PLAYER_FAILURE_URI`, `COGAME_HOST`,
`COGAME_PORT`, `COGAME_LOAD_REPLAY_URI`. Exit codes: **0** episode complete
(including `deadline` and `harness_fault`, whose artifacts are still written),
**2** missing or invalid config.

## Two name spaces

- **In-game alias** — slot 0 is `Ash`, slot 1 is `Basil`. This is the ONLY
  identity in `welcome`, in any observation, or in an opponent's history entry.
  A policy can never learn which player or policy it is facing.
- **Real player name** — `config.players[i].name`, recorded in the replay as
  `names[i]` and shown by the viewer. It is never sent to a player container.

## `welcome`

```json
{"type": "welcome", "protocol": "cogame.cogolf.v1", "game_version": "GV01",
 "slot": 0, "alias": "Ash", "opponent_alias": "Basil",
 "holes": 9, "hole_deadline_seconds": 40, "retry_deadline_seconds": 15,
 "rules": {"max_tests_per_hole": 5, "max_impl_chars": 4000,
           "max_test_name_chars": 40, "max_why_chars": 120,
           "max_args_chars": 400, "max_expect_chars": 400,
           "max_note_chars": 200, "max_message_bytes": 16384,
           "par_tests_per_hole": 4, "call_cpu_seconds": 1.0,
           "blocked": ["socket", "subprocess", "ctypes", "multiprocessing",
                       "threading", "file writes", "network"]},
 "episode": {"game_version": "GV01", "seats": 2, "slot": 0, "holes": 9,
             "deck": "core", "deck_version": "core-1", "seed": 1234567,
             "scoring": "zero_sum_v1"},
 "api_docs": "<how to write a submission: the schema, the legality gate, the scoring formula, one worked example>"}
```

Every episode parameter is stated at t=0; a policy must never infer one from
play. `welcome` carries no real player name.

## `observation`

One per hole, sent to **both seats in one parallel batch** (cogolf is a
simultaneous-decision game: the observations go out before either reply is
awaited). Re-sent once with `"retry": true` and the shorter deadline if the seat
missed the first one.

```json
{"type": "observation", "hole": 3, "deadline_seconds": 40, "retry": false,
 "observation": {
   "hole": 3, "holes": 9,
   "spec": {"key": "range_merge", "title": "Merge ranges",
            "prompt": "…verbatim, ≤1200 chars…",
            "signature": {"function": "solve",
                          "params": [{"name": "ranges", "type": "list[list[int]]"}],
                          "returns": "list[list[int]]"},
            "examples": [{"args": [[[1,3],[5,7]]], "expect": [[1,3],[5,7]]},
                         {"args": [[[1,5],[2,3]]], "expect": [[1,5]]}]},
   "you":      {"alias": "Ash",   "slot": 0, "score": 3},
   "opponent": {"alias": "Basil", "slot": 1, "score": -3},
   "history": [
     {"hole": 2, "spec_key": "median", "hole_score": 3,
      "your_tests":  [{"name": "even length", "args": [[1,2,3,4]], "expect": 2,
                       "legal": true, "legal_reason": null, "outcome": "breach"}],
      "their_tests": [{"name": "empty", "args": [[]], "expect": null, "why": "…",
                       "outcome": "held", "your_result": "null"}],
      "their_note": "aiming at the edges",
      "your_par_fails": 1, "their_par_fails": 2}],
   "rules": { /* the same object as welcome.rules */ }}}
```

**Visible to a seat:** the spec prompt, signature and two worked examples
(identical for both seats); its own alias, the opponent's alias, both cumulative
scores; and, for the last **4** resolved holes, its own tests with legality
verdicts and outcomes, the opponent's tests *with args, expect and why* plus
what your code returned, the opponent's `note`, and both seats' par-fail counts.

**Hidden from a seat:** the opponent's implementation source; the reference
implementation; the contents of the par tests (only counts are revealed); the
ambiguity note; which specs later holes will use; the opponent's real player
name and policy; anything about other episodes.

## `submission`

```json
{"type": "submission", "hole": 3,
 "impl": "def solve(ranges):\n    …",
 "tests": [{"name": "touching ends", "args": [[[1,2],[2,3]]], "expect": [[1,3]],
            "why": "spec says both ends are included"}],
 "note": "reading ends as inclusive"}
```

| field | cap | over-cap behaviour |
|---|---|---|
| whole message | 16384 **bytes** | malformed → retry → fallback |
| `impl` | 4000 **characters** | malformed → retry → fallback (never truncated: truncated code is broken code) |
| `tests` | 5 entries | entries past the cap are **dropped** |
| `tests[].name` | 40 characters | **truncated** on rune boundaries, `…` appended |
| `tests[].why` | 120 characters | **truncated** on rune boundaries |
| `tests[].args` | 400 characters of compact JSON | that test is `illegal: oversize` |
| `tests[].expect` | 400 characters of compact JSON | that test is `illegal: oversize` |
| `note` | 200 characters | **truncated** on rune boundaries |
| `observed` (server-side) | 300 characters | **truncated** on rune boundaries |

Every truncation is on **rune (Unicode code point) boundaries, never bytes**,
and every string that lands in the replay has its lone surrogates replaced with
`U+FFFD` and its control characters (other than `\n` and `\t`) stripped, so the
replay always parses under a strict UTF-8 JSON reader.

A reply whose `hole` is not the pending hole is dropped and counted
(`wrong_hole`); the hole keeps waiting until its deadline. A reply that arrives
after the fallback has been synthesised is ignored.

## How a hole resolves

1. **Draw and reveal** — the engine picks the hole's spec from the seeded sample
   and sends both seats the same observation, in one parallel batch.
2. **Collect** — wait until `hole_deadline_seconds` for both submissions,
   concurrently.
3. **Retry once** — a seat with no valid submission gets the identical
   observation again with `retry: true` and `retry_deadline_seconds`.
4. **Fallback** — a seat still without one gets the scripted `literalist`
   submission: a real, legal move. Its `fallbacks` counter and the matching
   `fallback_causes` counter (`timeout`, `malformed`, `oversize`,
   `disconnected`, `host_error`) increment. **No seat is ever removed from play.**
5. **Sanitise** — caps, rune-boundary truncation, control characters.
6. **Load** — each `impl` is loaded in the sandbox. One that fails to compile or
   defines no callable `solve` is `broken`, and **a broken impl fails every shot
   and every par test**.
7. **Legality gate** — every test of both seats is run against the spec's hidden
   `reference`. A test is legal iff its args are a JSON list matching the
   signature's arity, the reference neither raises nor exceeds its CPU budget,
   and `reference(*args)` equals `expect`. Otherwise it is illegal with a reason
   in `{arity, not_json, oversize, ref_error, ref_timeout, ref_mismatch,
   duplicate}`. Illegal tests never fire and never score; the reason comes back
   in the next observation.
8. **Cross-fire** — seat 0's legal tests run against seat 1's impl and vice
   versa. `held` when the defender returns an equal value; `breach` when it
   returns anything else, raises, exceeds its per-call CPU budget, returns a
   non-JSON value, or is broken.
9. **Par audit** — the spec's four hidden cases run against each impl.
10. **Score the hole** and emit the beats.
11. **Wall guard** — with less than `hole_reserve_seconds` left the episode stops
    with `reason: "deadline"`.

**Equality** is defined once (`server/cogame_cogolf/values.py`): tuples become
lists; only `null/bool/int/float/str/list/object` are representable; `NaN` and
`Infinity` are not; numbers compare by value so `1 == 1.0`; **`true` is never
equal to `1`**; object key order is irrelevant; strings compare by exact code
points. A value outside that set makes the call a breach (`bad_value`).

## `done` and `/global`

`done` is `{"type": "done", "result": {…}}`, broadcast **before** artifacts are
written; the server then closes the socket and the player process must exit 0.
The harness treats a close frame or a truncated read as a clean end.

`/global` is broadcast-only:

```json
{"type":"status","game_version":"GV01","aliases":["Ash","Basil"],
 "names":["daveey","daveey-1"],"holes":9,"hole":0,"scores":[0,0],"done":false}
{"type":"progress","hole":3,"scores":[3,-3],"killer":null}
{"type":"done","result":{…}}
```

`/healthz` and `/global` keep answering for a 20 s shutdown grace after the
artifacts are written, because the certification runner pings `/global` after
the player pods start.

## Degrade, never hang

| failure | what happens |
|---|---|
| no reply by the deadline | one retry, then the `literalist` fallback; cause `timeout` |
| reply not JSON / wrong `type` / `impl` not a string | retry, then fallback; cause `malformed` |
| message > 16 KB or `impl` > 4000 chars | retry, then fallback; cause `oversize` |
| seat never connects | play continues with the fallback every hole; cause `disconnected`; one `COGAME_PLAYER_FAILURE_URI` report |
| the LLM API errors, refuses or is unparseable | the **player** substitutes the scripted `literalist` submission — never a wire noop |
| a submitted impl loops, allocates or imports a blocked module | the sandbox kills it (1 s CPU per call, 6 s per batch, 256 MB address space); the affected calls become breaches |
| the sandbox subprocess dies mid-batch | the NDJSON results that arrived are kept; missing calls are `timeout` |
| the wall budget expires | the episode settles with `reason: "deadline"` on the last fully resolved hole |
