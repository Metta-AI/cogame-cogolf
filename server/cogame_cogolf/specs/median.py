"""median — the median of a non-empty list of integers."""

from ._util import load_impl

KEY = "median"
TITLE = "Median of a list"
PROMPT = """Write solve(xs) where xs is a non-empty list of integers, in any
order.

Return the median: the middle value once the list is in order. When the list
has an even number of elements there is no single middle value, so return the
middle value that comes first.

xs is never empty."""
SIGNATURE = {"function": "solve",
             "params": [{"name": "xs", "type": "list[int]"}],
             "returns": "int"}
EXAMPLES = [
    {"args": [[3, 1, 2]], "expect": 2},
    {"args": [[1, 2, 3, 4]], "expect": 2},
]

REFERENCE_IMPL = '''def solve(xs):
    if not xs:
        raise ValueError("median of an empty list")
    ordered = sorted(xs)
    return ordered[(len(ordered) - 1) // 2]
'''
reference = load_impl(REFERENCE_IMPL)

PAR_TESTS = [
    {"name": "unsorted even", "args": [[4, 1, 3, 2]], "expect": 2},
    {"name": "single", "args": [[7]], "expect": 7},
    {"name": "even pair", "args": [[1, 2]], "expect": 1},
    {"name": "unsorted pair", "args": [[5, 3]], "expect": 3},
]

SAFE_TESTS = [
    {"name": "even length", "args": [[1, 2, 3, 4]], "expect": 2,
     "why": "the first of the two middle values"},
    {"name": "two elements", "args": [[1, 3]], "expect": 1,
     "why": "with two elements the earlier middle wins"},
    {"name": "single element", "args": [[5]], "expect": 5,
     "why": "one element is its own median"},
    {"name": "odd length", "args": [[1, 2, 3]], "expect": 2,
     "why": "the plain middle of an odd list"},
    {"name": "repeated values", "args": [[2, 2, 4, 4]], "expect": 2,
     "why": "duplicates do not change the rule"},
]

EDGE_TESTS = [
    {"name": "unsorted input", "args": [[3, 1, 2]], "expect": 2,
     "why": "the list must be ordered before the middle is taken"},
    {"name": "unsorted even", "args": [[4, 1, 3, 2]], "expect": 2,
     "why": "ordering first changes which value is the middle"},
    {"name": "descending pair", "args": [[5, 3]], "expect": 3,
     "why": "the earlier middle after ordering, not before"},
    {"name": "empty is an error", "args": [[]], "expect": 0,
     "why": "betting that an empty list answers zero"},
    {"name": "mean of the middles", "args": [[1, 2, 3, 4]], "expect": 2.5,
     "why": "betting on the average of the two middle values"},
]

LITERAL_IMPL = '''def solve(xs):
    return xs[(len(xs) - 1) // 2]
'''

NAIVE_IMPL = '''def solve(xs):
    ordered = sorted(xs)
    n = len(ordered)
    if n % 2 == 1:
        return ordered[n // 2]
    return (ordered[n // 2 - 1] + ordered[n // 2]) / 2
'''

AMBIGUITY = "Order first, then take the LOWER middle: [1,2,3,4] is 2, not 2.5."
