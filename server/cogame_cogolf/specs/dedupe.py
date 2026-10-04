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
SIGNATURE = {
    "function": "solve",
    "params": [{"name": "xs", "type": "list"}],
    "returns": "list",
}
EXAMPLES = [
    {"args": [[3, 1, 3, 2]], "expect": [3, 1, 2]},
    {"args": [["b", "a", "b"]], "expect": ["b", "a"]},
]

REFERENCE_IMPL = """def solve(xs):
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
"""
reference = load_impl(REFERENCE_IMPL)

PAR_TESTS = [
    {"name": "gapped duplicate", "args": [[1, 2, 1]], "expect": [1, 2]},
    {"name": "descending order kept", "args": [[3, 1, 2]], "expect": [3, 1, 2]},
    {"name": "neighbouring pair", "args": [[1, 1]], "expect": [1]},
    {"name": "gapped strings", "args": [["a", "b", "a"]], "expect": ["a", "b"]},
]


AMBIGUITY = "Duplicates anywhere collapse, the first appearance survives, and the answer is NOT sorted."
