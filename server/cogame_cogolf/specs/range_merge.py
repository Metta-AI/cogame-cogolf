"""range_merge — merge overlapping inclusive ranges."""

from ._util import load_impl

KEY = "range_merge"
TITLE = "Merge ranges"
PROMPT = """Write solve(ranges) where ranges is a list of [start, end] pairs of
integers, each with start <= end, in any order.

Return the shortest list of [start, end] pairs, sorted by start, that covers
exactly the same numbers. Two ranges that share at least one number are one
range. A range covers BOTH of its endpoints: [1, 2] covers 1 and 2.

Ranges that merely touch across a gap, like [1, 2] and [4, 5], stay apart."""
SIGNATURE = {"function": "solve",
             "params": [{"name": "ranges", "type": "list[list[int]]"}],
             "returns": "list[list[int]]"}
EXAMPLES = [
    {"args": [[[1, 3], [5, 7]]], "expect": [[1, 3], [5, 7]]},
    {"args": [[[1, 5], [2, 3]]], "expect": [[1, 5]]},
]

REFERENCE_IMPL = '''def solve(ranges):
    ordered = sorted([list(r) for r in ranges])
    out = []
    for start, end in ordered:
        if out and start <= out[-1][1]:
            if end > out[-1][1]:
                out[-1][1] = end
        else:
            out.append([start, end])
    return out
'''
reference = load_impl(REFERENCE_IMPL)

PAR_TESTS = [
    {"name": "touching ends merge", "args": [[[1, 2], [2, 3]]],
     "expect": [[1, 3]]},
    {"name": "unsorted input", "args": [[[5, 6], [1, 2]]],
     "expect": [[1, 2], [5, 6]]},
    {"name": "empty", "args": [[]], "expect": []},
    {"name": "point range", "args": [[[1, 1]]], "expect": [[1, 1]]},
]

SAFE_TESTS = [
    {"name": "shared endpoint", "args": [[[1, 2], [2, 3]]],
     "expect": [[1, 3]], "why": "both ranges cover the number 2"},
    {"name": "disjoint", "args": [[[1, 3], [5, 7]]], "expect": [[1, 3], [5, 7]],
     "why": "a gap keeps them apart"},
    {"name": "empty list", "args": [[]], "expect": [],
     "why": "nothing to merge"},
    {"name": "nested", "args": [[[1, 5], [2, 3]]], "expect": [[1, 5]],
     "why": "a contained range disappears"},
    {"name": "two points", "args": [[[0, 0], [0, 0]]], "expect": [[0, 0]],
     "why": "a point range covers one number, shared by both"},
]

EDGE_TESTS = [
    {"name": "out of order", "args": [[[5, 6], [1, 2]]],
     "expect": [[1, 2], [5, 6]], "why": "the input order is not the answer's"},
    {"name": "chain out of order", "args": [[[3, 4], [1, 2], [2, 9]]],
     "expect": [[1, 9]], "why": "merging can cascade once sorted"},
    {"name": "adjacent integers", "args": [[[1, 2], [3, 4]]],
     "expect": [[1, 2], [3, 4]], "why": "1..2 and 3..4 share no number"},
    {"name": "gap merged", "args": [[[10, 11], [12, 13]]], "expect": [[10, 13]],
     "why": "betting that neighbouring integers merge"},
    {"name": "ends exclusive", "args": [[[1, 2], [2, 3]]],
     "expect": [[1, 2], [2, 3]], "why": "betting the end is not covered"},
]

LITERAL_IMPL = '''def solve(ranges):
    out = []
    for r in ranges:
        start, end = r[0], r[1]
        if out and start <= out[-1][1]:
            if end > out[-1][1]:
                out[-1][1] = end
        else:
            out.append([start, end])
    return out
'''

NAIVE_IMPL = '''def solve(ranges):
    ordered = sorted([list(r) for r in ranges])
    out = []
    for start, end in ordered:
        if out and start < out[-1][1]:
            if end > out[-1][1]:
                out[-1][1] = end
        else:
            out.append([start, end])
    return out
'''

AMBIGUITY = "Ends are inclusive, so [1,2] and [2,3] merge; [1,2] and [3,4] do not."
