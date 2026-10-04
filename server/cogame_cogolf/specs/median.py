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
SIGNATURE = {
    "function": "solve",
    "params": [{"name": "xs", "type": "list[int]"}],
    "returns": "int",
}
EXAMPLES = [
    {"args": [[3, 1, 2]], "expect": 2},
    {"args": [[1, 2, 3, 4]], "expect": 2},
]

REFERENCE_IMPL = """def solve(xs):
    if not xs:
        raise ValueError("median of an empty list")
    ordered = sorted(xs)
    return ordered[(len(ordered) - 1) // 2]
"""
reference = load_impl(REFERENCE_IMPL)

PAR_TESTS = [
    {"name": "unsorted even", "args": [[4, 1, 3, 2]], "expect": 2},
    {"name": "single", "args": [[7]], "expect": 7},
    {"name": "even pair", "args": [[1, 2]], "expect": 1},
    {"name": "unsorted pair", "args": [[5, 3]], "expect": 3},
]


AMBIGUITY = "Order first, then take the LOWER middle: [1,2,3,4] is 2, not 2.5."
