"""longest_run — the longest run of equal neighbouring elements."""

from ._util import load_impl

KEY = "longest_run"
TITLE = "Longest run"
PROMPT = """Write solve(xs) where xs is a list of integers.

Return the length of the longest run of equal elements in xs. A run is a
maximal block of neighbouring elements that are all equal to each other, so
[1, 1, 2, 2, 2, 1] has runs of length 2, 3 and 1 and the answer is 3.

A list with no elements has no runs at all."""
SIGNATURE = {"function": "solve",
             "params": [{"name": "xs", "type": "list[int]"}],
             "returns": "int"}
EXAMPLES = [
    {"args": [[1, 1, 2, 2, 2, 1]], "expect": 3},
    {"args": [[4]], "expect": 1},
]

REFERENCE_IMPL = '''def solve(xs):
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
'''
reference = load_impl(REFERENCE_IMPL)

PAR_TESTS = [
    {"name": "empty is zero", "args": [[]], "expect": 0},
    {"name": "alternating", "args": [[1, 2, 1, 2, 1]], "expect": 1},
    {"name": "pair", "args": [[7, 7]], "expect": 2},
    {"name": "split run", "args": [[3, 3, 1, 3]], "expect": 2},
]

SAFE_TESTS = [
    {"name": "empty list", "args": [[]], "expect": 0,
     "why": "a list with no elements has no runs"},
    {"name": "single element", "args": [[5]], "expect": 1,
     "why": "one element is a run of one"},
    {"name": "leading pair", "args": [[1, 1, 2]], "expect": 2,
     "why": "the longest neighbouring block wins"},
    {"name": "all distinct", "args": [[1, 2, 3]], "expect": 1,
     "why": "no two neighbours are equal"},
    {"name": "one long run", "args": [[4, 4, 4, 4]], "expect": 4,
     "why": "the whole list is one run"},
]

EDGE_TESTS = [
    {"name": "alternating values", "args": [[1, 2, 1, 2, 1]], "expect": 1,
     "why": "equal elements that are not neighbours are not one run"},
    {"name": "repeat after a gap", "args": [[3, 3, 1, 3]], "expect": 2,
     "why": "the trailing 3 does not extend the leading run"},
    {"name": "negatives", "args": [[-2, -2, -2, 0]], "expect": 3,
     "why": "negative values run like any other"},
    {"name": "empty raises", "args": [[]], "expect": None,
     "why": "betting that an empty list is an error"},
    {"name": "counts the value", "args": [[9, 9]], "expect": 9,
     "why": "betting the run's value is returned, not its length"},
]

LITERAL_IMPL = '''def solve(xs):
    counts = {}
    for x in xs:
        counts[x] = counts.get(x, 0) + 1
    if not counts:
        return 0
    return max(counts.values())
'''

NAIVE_IMPL = '''def solve(xs):
    best = 1
    run = 1
    for i in range(1, len(xs)):
        if xs[i] == xs[i - 1]:
            run += 1
            if run > best:
                best = run
        else:
            run = 1
    return best
'''

AMBIGUITY = "An empty list scores 0, and a run must be neighbouring: [1,2,1,2,1] is 1, not 3."
