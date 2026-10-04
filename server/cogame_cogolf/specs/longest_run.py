"""longest_run — the longest run of equal neighbouring elements."""

from ._util import load_impl

KEY = "longest_run"
TITLE = "Longest run"
PROMPT = """Write solve(xs) where xs is a list of integers.

Return the length of the longest run of equal elements in xs. A run is a
maximal block of neighbouring elements that are all equal to each other, so
[1, 1, 2, 2, 2, 1] has runs of length 2, 3 and 1 and the answer is 3.

A list with no elements has no runs at all."""
SIGNATURE = {
    "function": "solve",
    "params": [{"name": "xs", "type": "list[int]"}],
    "returns": "int",
}
EXAMPLES = [
    {"args": [[1, 1, 2, 2, 2, 1]], "expect": 3},
    {"args": [[4]], "expect": 1},
]

REFERENCE_IMPL = """def solve(xs):
    best = 0
    run = 0
    prev = object()
    for x in xs:
        if run and x == prev:
            run += 1
        else:
            run = 1
        prev = x
        if run > best:
            best = run
    return best
"""
reference = load_impl(REFERENCE_IMPL)

PAR_TESTS = [
    {"name": "empty is zero", "args": [[]], "expect": 0},
    {"name": "alternating", "args": [[1, 2, 1, 2, 1]], "expect": 1},
    {"name": "pair", "args": [[7, 7]], "expect": 2},
    {"name": "split run", "args": [[3, 3, 1, 3]], "expect": 2},
]


AMBIGUITY = (
    "An empty list scores 0, and a run must be neighbouring: [1,2,1,2,1] is 1, not 3."
)
