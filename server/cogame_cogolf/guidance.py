"""Published submission guidance shared by engine windows and players."""

API_DOCS = """COGOLF — how to write a submission
=================================

You are one of two code agents playing a nine-hole match. Every hole shows
ONE deliberately ambiguous spec. You reply with one implementation of
solve(...) and up to five test cases. Your tests are fired at your
opponent's implementation, theirs at yours, and a hidden four-case audit
runs against your code.

THE REPLY
---------
Send exactly one JSON object on the websocket:

  {"type": "submission",
   "hole": 3,
   "impl": "def solve(ranges):\\n    ...",
   "tests": [{"name": "touching ends",
              "args": [[[1, 2], [2, 3]]],
              "expect": [[1, 3]],
              "why": "the spec says both ends are included"}],
   "note": "reading ends as inclusive"}

  impl    Python source, stdlib only. It must define solve(...) with the
          signature the spec gives. No sockets, subprocesses, ctypes,
          multiprocessing, threading, file writes or network access; each
          call gets a hard CPU budget (welcome.rules.call_cpu_seconds).
  tests   Up to rules.max_tests_per_hole entries. `args` is the ARGUMENT
          LIST for one solve(*args) call; `expect` is the exact JSON value
          the call must return; `why` is one short sentence naming the
          clause you are testing.
  note    One line, echoed to your opponent in their next observation.

Values are JSON only: null, bool, int, float, str, list, object with string
keys. Numbers compare by value (1 == 1.0), true is NEVER equal to 1, object
key order does not matter, and NaN / Infinity are not values.

THE LEGALITY GATE
-----------------
A hidden REFERENCE implementation settles every ambiguous clause. Before a
test of yours is fired it is run against that reference. It counts only if

  * `args` is a list whose length matches the signature's parameter count,
  * the reference neither raises nor exceeds its CPU budget on it,
  * the reference's answer equals your `expect`, and
  * you have not already fired the same `args` this hole.

Otherwise the test is ILLEGAL: it never fires, it never scores, and the
reason (arity, not_json, oversize, ref_error, ref_timeout, ref_mismatch,
duplicate) comes back to you in the next observation. Illegal tests are how
you learn what the reference actually does — but they cost you a shot.

THE SCORE
---------
For each hole, with you as i and your opponent as j:

  hole_score[i] = (your breaching tests + their audit failures)
                - (their breaching tests + your audit failures)

A test BREACHES when the defending implementation returns something else,
raises, times out, returns a non-JSON value, or failed to load at all.
Otherwise it HELD. The audit is the spec's four hidden par cases run
against your own implementation. The match is zero-sum: what you gain, your
opponent loses. Higher is better.

HOW TO PLAY WELL
----------------
1. Read the prompt for the ONE clause that admits two honest readings, and
   pick the reading that is consistent with BOTH worked examples.
2. Implement that reading defensively: nothing should raise. An
   implementation that dies on an edge case fails every shot aimed there
   AND the hidden audit.
3. Spend your tests on the clause you picked — small, clearly legal cases
   a careless reader would get wrong. A test the reference rejects is a
   wasted shot.
4. Read the history: your own illegal verdicts tell you what the reference
   decided, and their tests (you see their args, expect and why) tell you
   where they think you are wrong.

A WORKED HOLE
-------------
Spec: "merge overlapping [start, end] ranges; a range covers BOTH of its
endpoints". The ambiguous clause is whether [1,2] and [2,3] overlap. They
share the number 2, so they merge.

  {"type": "submission", "hole": 1,
   "impl": "def solve(ranges):\\n    out = []\\n    for start, end in sorted(ranges):\\n        if out and start <= out[-1][1]:\\n            out[-1][1] = max(out[-1][1], end)\\n        else:\\n            out.append([start, end])\\n    return out",
   "tests": [{"name": "shared endpoint", "args": [[[1, 2], [2, 3]]],
              "expect": [[1, 3]],
              "why": "both ranges cover the number 2"},
             {"name": "true gap", "args": [[[1, 2], [4, 5]]],
              "expect": [[1, 2], [4, 5]],
              "why": "3 is in neither range"}],
   "note": "ends are inclusive"}

DEADLINES
---------
The observation carries `deadline_seconds`. Miss it and you get ONE retry
with a shorter deadline; miss that and a scripted `literalist` submission
is played for you — a legal move, but a weak one. Answer every hole.
"""
