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
SIGNATURE = {"function": "solve",
             "params": [{"name": "xs", "type": "list"},
                        {"name": "k", "type": "int"}],
             "returns": "list"}
EXAMPLES = [
    {"args": [[3, 1, 3, 1, 2], 2], "expect": [3, 1]},
    {"args": [[1, 2, 3], 0], "expect": []},
]

REFERENCE_IMPL = '''def solve(xs, k):
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
'''
reference = load_impl(REFERENCE_IMPL)

PAR_TESTS = [
    {"name": "tie by first appearance", "args": [[3, 1, 3, 1, 2], 2],
     "expect": [3, 1]},
    {"name": "k over distinct", "args": [[1, 2], 5], "expect": [1, 2]},
    {"name": "single item", "args": [[7], 1], "expect": [7]},
    {"name": "strings over distinct", "args": [["z", "a", "z", "a"], 3],
     "expect": ["z", "a"]},
]

SAFE_TESTS = [
    {"name": "tie keeps order", "args": [[3, 1, 3, 1, 2], 2], "expect": [3, 1],
     "why": "3 appeared before 1 and both occur twice"},
    {"name": "string tie", "args": [["b", "a", "b", "a"], 2],
     "expect": ["b", "a"], "why": "first appearance, not alphabetical"},
    {"name": "clear winner", "args": [[5, 5, 4], 1], "expect": [5],
     "why": "the most frequent item alone"},
    {"name": "k is zero", "args": [[1, 2, 3], 0], "expect": [],
     "why": "k may be 0"},
    {"name": "another tie", "args": [[2, 1, 2, 1], 2], "expect": [2, 1],
     "why": "the earlier of two equally frequent items leads"},
]

EDGE_TESTS = [
    {"name": "k over distinct", "args": [[1, 2], 5], "expect": [1, 2],
     "why": "k is a request, not a promise"},
    {"name": "one item, big k", "args": [[1], 3], "expect": [1],
     "why": "fewer distinct items than asked for"},
    {"name": "empty list", "args": [[], 2], "expect": [],
     "why": "no items to rank"},
    {"name": "k over distinct errors", "args": [[7, 8], 9], "expect": None,
     "why": "betting that too large a k is an error"},
    {"name": "ties sorted by value", "args": [[3, 1, 3, 1, 2], 2],
     "expect": [1, 3], "why": "betting ties break by value"},
]

LITERAL_IMPL = '''def solve(xs, k):
    counts = {}
    for x in xs:
        counts[x] = counts.get(x, 0) + 1
    ranked = sorted(counts, key=lambda item: -counts[item])
    return [ranked[i] for i in range(k)]
'''

NAIVE_IMPL = '''def solve(xs, k):
    counts = {}
    for x in xs:
        counts[x] = counts.get(x, 0) + 1
    ranked = sorted(counts, key=lambda item: (-counts[item], item))
    return ranked[:k]
'''

AMBIGUITY = "Ties break by first appearance, k larger than the distinct count returns all of them."
