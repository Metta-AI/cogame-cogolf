"""dedupe — remove duplicate items, keeping the first of each."""

from ._util import load_impl

KEY = "dedupe"
TITLE = "Remove duplicates"
PROMPT = """Write solve(xs) where xs is a list of JSON values.

Return a list holding each distinct item of xs exactly once. An item is a
duplicate of an earlier item anywhere in the list, not only of the one right
before it, and the item that survives is the first one, in the position it
first appeared.

The result is not sorted; the order of first appearance is the answer."""
SIGNATURE = {"function": "solve",
             "params": [{"name": "xs", "type": "list"}],
             "returns": "list"}
EXAMPLES = [
    {"args": [[3, 1, 3, 2]], "expect": [3, 1, 2]},
    {"args": [["b", "a", "b"]], "expect": ["b", "a"]},
]

REFERENCE_IMPL = '''def solve(xs):
    out = []
    for x in xs:
        seen = False
        for y in out:
            if type(x) is type(y) and x == y:
                seen = True
                break
        if not seen:
            out.append(x)
    return out
'''
reference = load_impl(REFERENCE_IMPL)

PAR_TESTS = [
    {"name": "gapped duplicate", "args": [[1, 2, 1]], "expect": [1, 2]},
    {"name": "descending order kept", "args": [[3, 1, 2]], "expect": [3, 1, 2]},
    {"name": "neighbouring pair", "args": [[1, 1]], "expect": [1]},
    {"name": "gapped strings", "args": [["a", "b", "a"]], "expect": ["a", "b"]},
]

SAFE_TESTS = [
    {"name": "order is kept", "args": [[3, 1, 2]], "expect": [3, 1, 2],
     "why": "the result is not sorted"},
    {"name": "strings keep order", "args": [["b", "a"]], "expect": ["b", "a"],
     "why": "first appearance, not alphabetical"},
    {"name": "leading pair", "args": [[2, 2, 1]], "expect": [2, 1],
     "why": "the first of a pair survives, in place"},
    {"name": "middle pair", "args": [[5, 4, 4, 3]], "expect": [5, 4, 3],
     "why": "descending input stays descending"},
    {"name": "empty list", "args": [[]], "expect": [],
     "why": "nothing to deduplicate"},
]

EDGE_TESTS = [
    {"name": "duplicate with a gap", "args": [[1, 2, 1]], "expect": [1, 2],
     "why": "a duplicate need not be adjacent"},
    {"name": "interleaved", "args": [[1, 3, 1, 3]], "expect": [1, 3],
     "why": "two interleaved values collapse to two items"},
    {"name": "nested lists", "args": [[[1], [1], [2]]], "expect": [[1], [2]],
     "why": "items may be lists, which compare by value"},
    {"name": "adjacent only", "args": [[2, 3, 2]], "expect": [2, 3, 2],
     "why": "betting only neighbouring duplicates are removed"},
    {"name": "sorted result", "args": [[3, 1, 2]], "expect": [1, 2, 3],
     "why": "betting the answer comes back sorted"},
]

LITERAL_IMPL = '''def solve(xs):
    out = []
    for x in xs:
        if not out or out[-1] != x:
            out.append(x)
    return out
'''

NAIVE_IMPL = '''def solve(xs):
    return sorted(set(xs))
'''

AMBIGUITY = "Duplicates anywhere collapse, the first appearance survives, and the answer is NOT sorted."
