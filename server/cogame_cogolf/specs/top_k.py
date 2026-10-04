"""top_k — the k most frequent items of a list."""

from ._util import load_impl

KEY = "top_k"
TITLE = "Top k by frequency"
PROMPT = """Write solve(xs, k) where xs is a list of JSON values and k is a
non-negative integer.

Return the k items that occur most often in xs, most frequent first, each
item listed once. When two items occur equally often, the one that appeared
earlier in xs comes first.

k is a request, not a promise: if xs holds fewer than k distinct items,
return the ones it has. k may be 0."""
SIGNATURE = {
    "function": "solve",
    "params": [{"name": "xs", "type": "list"}, {"name": "k", "type": "int"}],
    "returns": "list",
}
EXAMPLES = [
    {"args": [[3, 1, 3, 1, 2], 2], "expect": [3, 1]},
    {"args": [[1, 2, 3], 0], "expect": []},
]

REFERENCE_IMPL = """def solve(xs, k):
    order = []
    counts = {}
    for x in xs:
        key = (type(x).__name__, repr(x))
        if key not in counts:
            counts[key] = [0, len(order), x]
            order.append(key)
        counts[key][0] += 1
    ranked = sorted(order, key=lambda key: (-counts[key][0], counts[key][1]))
    return [counts[key][2] for key in ranked[:max(0, k)]]
"""
reference = load_impl(REFERENCE_IMPL)

PAR_TESTS = [
    {"name": "tie by first appearance", "args": [[3, 1, 3, 1, 2], 2], "expect": [3, 1]},
    {"name": "k over distinct", "args": [[1, 2], 5], "expect": [1, 2]},
    {"name": "single item", "args": [[7], 1], "expect": [7]},
    {
        "name": "strings over distinct",
        "args": [["z", "a", "z", "a"], 3],
        "expect": ["z", "a"],
    },
]


AMBIGUITY = "Ties break by first appearance, k larger than the distinct count returns all of them."
