# Cogolf — rules & submission contract

Two code agents play nine holes. Every hole is the same shape and the whole game
is in one formula.

## The hole

1. One **spec** is revealed to both seats: a prompt of at most 1200 characters,
   the signature of `solve(...)`, and two worked examples. The prompt is
   deliberately ambiguous in exactly one clause.
2. Both seats **simultaneously** submit an implementation of `solve(...)` and up
   to five test cases. Neither sees the other's submission before submitting.
3. The harness **cross-fires**: your legal tests run against their
   implementation, theirs against yours.
4. A hidden **par suite** of four cases runs against each implementation.
5. The hole is scored.

## Scoring

For hole `h`, seat `i`, opponent `j`:

```
hole_score[i][h] = (breaches[i][h] + par_fails[j][h])
                 - (breaches[j][h] + par_fails[i][h])
scores[i]        = sum over resolved holes
```

- `breaches[i][h]` — your **legal** tests that made their implementation fail
  (0..5).
- `par_fails[i][h]` — hidden par cases your OWN implementation failed (0..4).
- Illegal tests contribute nothing at all.

Higher is better. The pair is exactly antisymmetric, so
`hole_score[0][h] == -hole_score[1][h]` and `scores[0] + scores[1] == 0`. A hole
ranges ±9 and a nine-hole match ±81. The league ranks by `scores`; `[0, 0]` is a
draw and there is no secondary tiebreak inside the game.

The `par_fails` term is what stops a degenerate "write no tests, write no code"
equilibrium: even against a silent opponent, code that fails the hidden audit
loses points.

## What makes a test legal

A hidden **reference implementation** is the only authority on the ambiguous
clause. Before a test of yours fires, it is run against the reference. It counts
only if:

- `args` is a JSON list whose length matches the signature's parameter count,
- the reference neither raises nor exceeds its CPU budget on it,
- the reference's answer equals your `expect`, and
- you have not already fired the same `args` this hole.

Otherwise it is **illegal** — it never fires and never scores — with one of:

| reason | meaning |
|---|---|
| `arity` | wrong number of arguments for `solve` |
| `not_json` | `args` is not a list, or a value has no JSON form |
| `oversize` | `args` or `expect` is over 400 characters of compact JSON |
| `ref_error` | the reference raised on these arguments |
| `ref_timeout` | the reference exceeded its CPU budget |
| `ref_mismatch` | the reference disagrees with your `expect` — you read the clause the other way |
| `duplicate` | the same arguments as an earlier legal test of yours this hole |

Illegal verdicts and their reasons come back to their author in the next
observation. They are the game's feedback channel: a `ref_mismatch` is the
reference telling you which reading is real.

## What makes a shot breach

A shot **held** when the defending implementation returns a value equal to
`expect`. It **breached** when the defender:

- returned anything else,
- raised,
- exceeded its per-call CPU budget (1 second by default),
- returned a value with no JSON representation (`NaN`, a set, an object), or
- failed to load at all (a broken implementation fails every shot and every par
  case).

**Equality** is one rule, used everywhere: tuples become lists; numbers compare
by value so `1 == 1.0`; **`true` is never equal to `1`**; object key order does
not matter; strings compare by exact code points; `NaN` and `Infinity` are not
values.

## The sandbox

Every submitted implementation runs in its own subprocess, never in the server's
interpreter:

- launched with `-I -S` (no site, no user site, no environment import path), a
  scrubbed environment and a fresh empty working directory;
- `RLIMIT_AS` 256 MB, `RLIMIT_FSIZE` 0 (any file write fails), `RLIMIT_NPROC` 0
  (no forks), `RLIMIT_NOFILE` 16;
- an audit hook denying the `socket.`, `subprocess.`, `os.exec`, `ctypes.`,
  `shutil.` and `urllib.` event families, `os.system`, imports of
  `socket`/`subprocess`/`ctypes`/`multiprocessing`/`threading`, and any `open`
  in a write mode;
- `setuid` to an unprivileged uid when the process starts as root;
- 1 second of CPU per call (`ITIMER_VIRTUAL`), 6 seconds of wall clock per
  batch. Results are written as NDJSON and flushed before the next call starts,
  so a batch killed at the cap still yields every result it produced.

Write ordinary stdlib Python. `import math` is fine; `import socket` is not.

## Deadlines and the fallback

You have `hole_deadline_seconds` (40 by default) to answer. Miss it and the
identical observation comes again with `retry: true` and a shorter deadline.
Miss that and the engine plays the scripted `literalist` submission for you — a
real, legal move, but a weak one, and the replay marks the hole with a
`FALLBACK` chip. **No seat is ever removed from play and every hole is resolved
for both seats.**

## Ending

| `reason` | when | scores |
|---|---|---|
| `complete` | all holes resolved | the full match |
| `deadline` | the wall-clock budget expired, or too little of it was left to start another hole | as of the last **fully resolved** hole; an interrupted hole is discarded, never half-scored |
| `harness_fault` | the sandbox runner could not be spawned or the deck failed to load mid-episode | the same; artifacts are still written and the process exits 0 |

## The killer test

For the endcard: among all breaching shots of the winning seat, the one in the
hole with the largest score swing, tie-broken by earliest hole then lowest test
index — recorded with its author's one-line `why`. A draw, or a match with no
breach at all, gives `killer_test: null` and the endcard reads
`NO BREACH — DRAWN MATCH`.

## The deck

The twelve `core` specs are public (this repository is public) and identical for
both seats; the reference implementations and the par cases stay inside the game
container. A memorised deck helps both champions equally. Each hole draws
without replacement from `random.Random(seed).sample(sorted(deck_keys), holes)`,
and the resolved seed is recorded in the results doc and the replay, so a match
is reproducible from its own bytes.
