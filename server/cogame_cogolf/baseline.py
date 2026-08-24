"""The two scripted baselines, shared by the engine and the players.

Both are pure functions ``baseline(spec, hole) -> submission dict``. The
module imports only the spec deck and the stdlib, so the engine can call it
for the degrade path (a seat that missed its deadline still plays a real,
legal move) and ``players/main.py`` can call it as a policy.

``literalist``  the reference-aligned reader: a plain implementation that
                follows the prompt's text, and the spec's SAFE_TESTS, which
                are reference-consistent by construction — every shot legal.
``pedant``      the edge-case sniper: an implementation that is right on the
                common path and wrong on a DIFFERENT clause than the
                literalist's, plus the spec's aggressive EDGE_TESTS, some of
                which the reference rejects. Those come back illegal, which
                is exactly the lesson the baseline exists to demonstrate.

Both are deterministic: same spec, same submission.
"""

from __future__ import annotations

from .contract import (MAX_IMPL_CHARS, MAX_TESTS_PER_HOLE, MSG_SUBMISSION)

BASELINE_NAMES = ("literalist", "pedant")

STUB_NOTE = "unknown spec: echoing the worked example"


class UnknownBaseline(ValueError):
    """No such scripted baseline."""


def _tests(cases, max_tests: int) -> list[dict]:
    out = []
    for case in list(cases)[:max_tests]:
        out.append({
            "name": str(case.get("name", "case"))[:40],
            "args": list(case.get("args") or []),
            "expect": case.get("expect"),
            "why": str(case.get("why", ""))[:120],
        })
    return out


def _stub(spec, hole: int, max_tests: int) -> dict:
    """A deck extended without updating the baselines still plays.

    The implementation echoes the first worked example's answer for
    matching arguments and None otherwise; the tests are the spec's own
    examples. Always bounded, always schema-valid.
    """
    examples = list(getattr(spec, "EXAMPLES", []) or [])
    first = examples[0] if examples else {"args": [], "expect": None}
    impl = (
        "def solve(*args):\n"
        f"    known = {first.get('args', [])!r}\n"
        f"    if list(args) == known:\n"
        f"        return {first.get('expect')!r}\n"
        "    return None\n"
    )
    tests = _tests([{"name": f"example {i + 1}", "args": ex.get("args", []),
                     "expect": ex.get("expect"),
                     "why": "the spec's own worked example"}
                    for i, ex in enumerate(examples)], max_tests)
    return _message(hole, impl, tests, STUB_NOTE)


def _message(hole: int, impl: str, tests: list[dict], note: str) -> dict:
    return {"type": MSG_SUBMISSION, "hole": int(hole),
            "impl": impl[:MAX_IMPL_CHARS], "tests": tests, "note": note}


def literalist(spec, hole: int,
               max_tests: int = MAX_TESTS_PER_HOLE) -> dict:
    """Play the text as written; fire the safe, reference-aligned shots."""
    impl = getattr(spec, "LITERAL_IMPL", None)
    cases = getattr(spec, "SAFE_TESTS", None)
    if not isinstance(impl, str) or not cases:
        return _stub(spec, hole, max_tests)
    return _message(hole, impl, _tests(cases, max_tests),
                    "playing the text as written")


def pedant(spec, hole: int, max_tests: int = MAX_TESTS_PER_HOLE) -> dict:
    """Aim at the edges; some shots come back illegal, by design."""
    impl = getattr(spec, "NAIVE_IMPL", None)
    cases = getattr(spec, "EDGE_TESTS", None)
    if not isinstance(impl, str) or not cases:
        return _stub(spec, hole, max_tests)
    return _message(hole, impl, _tests(cases, max_tests), "aiming at the edges")


BASELINES = {"literalist": literalist, "pedant": pedant}


def baseline(name: str, spec, hole: int,
             max_tests: int = MAX_TESTS_PER_HOLE) -> dict:
    """``BASELINES[name]`` with a clear error for a typo'd name."""
    try:
        policy = BASELINES[name]
    except KeyError:
        raise UnknownBaseline(
            f"unknown baseline {name!r}; known: {sorted(BASELINES)}") from None
    return policy(spec, hole, max_tests)
